from kubernetes import client, config, watch
import time
import pickle
import base64
import logging

try:
    # local load kubeconfig
    config.load_kube_config("PATH TO KUBECONFIG")
except:
    # load_kube_config throws if there is no config, but does not document what it throws, so I can't rely on any particular type here
    config.load_incluster_config()

v1 = client.CoreV1Api()
api = client.AppsV1Api()

# Config logging
logging.basicConfig(
    format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=logging.INFO
)


def main():
    last_seen_version = None
    w = watch.Watch()
    resource = api.list_stateful_set_for_all_namespaces

    logging.info("start watching")
    while True:
        stream = w.stream(resource, timeout_seconds=100)
        try:
            for event in stream:
                sts_name = event["object"].metadata.name
                namespace = event["object"].metadata.namespace
                last_seen_version = event["object"].metadata.resource_version
                annotations = event["object"].metadata.annotations or {}
                prefix = "resize-statefulset-operator/resize-"
                vc_indexes = sorted(
                    int(key[len(prefix):])
                    for key in annotations
                    if key.startswith(prefix) and key[len(prefix):].isdigit()
                )

                if vc_indexes:
                    for vc in vc_indexes:
                        if vc >= len(event["object"].spec.volume_claim_templates):
                            logging.warning("Ignoring resize annotation for missing template %s", vc)
                            continue
                        sts_name = event["object"].metadata.name
                        replicas = event["object"].status.replicas
                        namespace = event["object"].metadata.namespace
                        pvc_name = (
                            event["object"]
                            .spec.volume_claim_templates[vc]
                            .metadata.name
                        )
                        statefulset = api.read_namespaced_stateful_set(
                            sts_name, namespace
                        )
                        storage = statefulset.spec.volume_claim_templates[
                            vc
                        ].spec.resources.requests["storage"]

                        # Turn str into int
                        storage_size = storage.replace("Gi", "")
                        annotation_size = statefulset.metadata.annotations[
                            f"resize-statefulset-operator/resize-{vc}"
                        ].replace("Gi", "")
                        storage_size_int = int(storage_size)
                        annotation_size_int = int(annotation_size)

                        # logging.info("Event: %s %s" % (event['type'], event['object'].kind))
                        # Compare the vc_template_size and annotations_size
                        if annotation_size_int > storage_size_int:
                            logging.info(
                                "Event: %s %s %s from %s to %s in %s"
                                % (
                                    event["type"],
                                    event["object"].kind,
                                    sts_name,
                                    storage,
                                    event["object"].metadata.annotations[
                                        f"resize-statefulset-operator/resize-{vc}"
                                    ],
                                    namespace,
                                )
                            )
                            logging.info(
                                "StatefulSet storage size: %s" % (storage_size_int)
                            )
                            logging.info(
                                f"StatefulSet resize-statefulset-operator/resize-{vc}: %s"
                                % (annotation_size_int)
                            )

                            modify_pvc(
                                sts_name,
                                pvc_name,
                                annotation_size,
                                replicas,
                                namespace,
                                vc,
                            )
        except ConnectionResetError as e:
            logging.info(e)
        logging.info("To avoid the ConnectionResetError. Timeout...")
        time.sleep(1)


def modify_pvc(sts, pvc_n, size, replica, ns, vc):
    for rep in range(replica):
        pvc_name = str(pvc_n) + "-" + sts + "-" + str(rep)
        # Get PVC information
        pvc = v1.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=ns)
        old_size = pvc.spec.resources.requests["storage"]
        # update pvc size
        pvc.spec.resources.requests["storage"] = size + "Gi"
        v1.replace_namespaced_persistent_volume_claim(
            name=pvc_name, namespace=ns, body=pvc
        )  # replace pvc
        logging.info(
            "update PVC %s from %s to %s"
            % (pvc_name, old_size, pvc.spec.resources.requests["storage"])
        )

    modify_sts_to_configmap(sts, size, ns, vc)


def modify_sts_to_configmap(statefulset_name, new_size, ns, vc):
    # Get the statefulset information
    statefulset = api.read_namespaced_stateful_set(statefulset_name, ns)
    statefulset.spec.volume_claim_templates[vc].spec.resources.requests["storage"] = (
        new_size + "Gi"
    )

    # Remove resourceVersion because it will be set automatically while the objects is created
    statefulset.metadata.resource_version = None

    # Python pickle save statefulset information
    statefulset_pickle = base64.b64encode(pickle.dumps(statefulset)).decode("utf-8")

    # Create&update config map
    config_name = statefulset_name + "-backup"
    configmap = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=config_name),
        data={"statefulset": statefulset_pickle},
    )
    try:
        v1.create_namespaced_config_map(namespace=ns, body=configmap)
        logging.info("ConfigMap created")
    except client.rest.ApiException as est:
        if est.status == 409:
            v1.replace_namespaced_config_map(
                name=config_name, namespace=ns, body=configmap
            )
            logging.info("ConfigMap updated")
        else:
            raise est

    update_statefulset(statefulset_name, config_name, ns, vc)


def update_statefulset(sts, config_name, ns, vc):

    logging.info("Deleting oringinal Statefulset...")
    api.delete_namespaced_stateful_set(sts, ns, propagation_policy="Orphan")

    while True:
        try:
            api.read_namespaced_stateful_set(sts, ns)
            time.sleep(1)
        except client.rest.ApiException as est:
            if est.status == 404:
                logging.info("Delete succussfully")
                time.sleep(1)
                break
            else:
                logging.info("Error: {}".format(est))
    # Get the ConfigMap object
    config_map = v1.read_namespaced_config_map(name=config_name, namespace=ns)

    # Access the data in the ConfigMap
    statefulset = config_map.data["statefulset"]

    update_sts = pickle.loads(base64.b64decode(statefulset))
    api.create_namespaced_stateful_set(namespace=ns, body=update_sts)

    read_update = api.read_namespaced_stateful_set(sts, ns)
    logging.info(
        "Storage size change to %s UPDATE SUCCESSFULLY!!!!"
        % (
            read_update.spec.volume_claim_templates[vc].spec.resources.requests[
                "storage"
            ]
        )
    )


if __name__ == "__main__":
    main()
