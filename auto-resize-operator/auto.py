import sys
import logging
import re
from decimal import Decimal, ROUND_CEILING
from kubernetes.client.api import core_v1_api
from kubernetes import client, config, watch
from kubernetes.stream import stream
import base64


def transcript(size):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGTPE]?)", size.strip())
    if match is None:
        raise ValueError("Unsupported df size: %r" % size)
    powers = {"": -3, "K": -2, "M": -1, "G": 0, "T": 1, "P": 2, "E": 3}
    gibibytes = Decimal(match.group(1)) * (Decimal(1024) ** powers[match.group(2)])
    return int(gibibytes.to_integral_value(rounding=ROUND_CEILING))


def get_pvc(sts, vc):  # get PVC name for a volume claim template
    try:
        pvc_name = sts.spec.volume_claim_templates[vc].metadata.name
        print("PVC: %s" % (pvc_name))
        return pvc_name
    except IndexError:
        return None


def get_mountPath(sts, container_num, pvc_name):
    if container_num != 0:
        for ctr in range(container_num):
            ctr = int(ctr)
            container = sts.spec.template.spec.containers[ctr].name
            volumes_num = len(sts.spec.template.spec.containers[ctr].volume_mounts)
            print("Container %s volumeMounts: %s" % (container, volumes_num))
            for vm in range(volumes_num):
                vm = int(vm)
                if (
                    sts.spec.template.spec.containers[ctr].volume_mounts[vm].name
                    == pvc_name
                ):
                    mountPath = (
                        sts.spec.template.spec.containers[ctr]
                        .volume_mounts[vm]
                        .mount_path
                    )
                    # logging.info(mountPath)
                    return ctr, mountPath


# Retrieve the container name and ID of the first container in the pod
def exec(api, pod, ctr, mountPath):
    container = pod.spec.containers[ctr]
    container_name = container.name
    pod_name = pod.metadata.name
    namespace = pod.metadata.namespace

    usage = stream(
        api.connect_get_namespaced_pod_exec,
        name=pod_name,
        namespace=namespace,
        container=container_name,
        command=[
            "/bin/sh",
            "-c",
            f"df -h --output=pcent '{mountPath}' | tail -n 1 | awk '{{print $1}}'",
        ],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    size = stream(
        api.connect_get_namespaced_pod_exec,
        name=pod_name,
        namespace=namespace,
        container=container_name,
        command=[
            "/bin/sh",
            "-c",
            f"df -h --output=size '{mountPath}' | tail -n 1 | awk '{{print $1}}'",
        ],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    logging.info("%s size: %s usage: %s" % (pod_name, size, usage))
    size = transcript(size)
    return int(usage.replace("%", "")), size


def main():

    logging.basicConfig(
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )

    try:
        # local load kubeconfig
        config.load_kube_config("PATH TO KUBECONFIG")
    except:
        # load_kube_config throws if there is no config, but does not document what it throws, so I can't rely on any particular type here
        config.load_incluster_config()

    api = client.CoreV1Api()
    app = client.AppsV1Api()

    last_seen_version = None
    w = watch.Watch()
    # Add label
    # If I add label, the type of resource would become 'V1StatefulSetList'.
    # 'V1StatefulSetList' object is not callable so we cannot watch resourse with label
    # resource = app.list_stateful_set_for_all_namespaces(label_selector='resize-statefulset-operator/auto-scaled')

    resource = app.list_stateful_set_for_all_namespaces  # (type=method)
    logging.info("start watching")
    while True:
        stream = w.stream(
            resource, timeout_seconds=180
        )  # Will get all resources when timeout
        try:
            for event in stream:
                if event["object"].metadata.labels is not None:
                    if (
                        event["object"].metadata.labels.get(
                            "resize-statefulset-operator/auto-scaled"
                        ) == "true"
                    ):
                        sts_name = event["object"].metadata.name
                        namespace = event["object"].metadata.namespace
                        last_seen_version = event["object"].metadata.resource_version

                        sts = app.read_namespaced_stateful_set(sts_name, namespace)
                        try:
                            vc_num = len(sts.spec.volume_claim_templates)
                            container_num = len(sts.spec.template.spec.containers)
                            replicas = sts.status.replicas
                            logging.info(sts_name)
                            print("volumeclaimtemplates: %s" % (vc_num))
                            print("containers: %s" % (container_num))

                            for vc in range(vc_num):
                                pvc_name = get_pvc(sts, vc)
                                if pvc_name is None:
                                    continue
                                # The function use to get corresponding container and mountPath
                                mount = get_mountPath(sts, container_num, pvc_name)
                                if mount is None:
                                    logging.warning("No mount found for PVC %s", pvc_name)
                                    continue
                                ctr, mountPath = mount

                                for rep in range(replicas):
                                    pod_name = sts_name + "-" + str(rep)
                                    pod = api.read_namespaced_pod(pod_name, namespace)
                                    usage, size = exec(api, pod, ctr, mountPath)

                                    if usage >= 80:
                                        print("Usage over 80% !!")
                                        for i in range(12):
                                            if 2**i <= size < 2 ** (i + 1):
                                                new_size = 2 ** (i + 1)
                                                print("New size: %s" % (new_size))
                                                break
                                            else:
                                                continue
                                        new_size = str(new_size) + "Gi"
                                        sts.spec.volume_claim_templates[
                                            vc
                                        ].spec.resources.requests["storage"] = new_size
                                        app.patch_namespaced_stateful_set(
                                            sts_name, namespace, body=sts
                                        )
                                    elif usage < 80:
                                        print("Don't worry\n")
                        except Exception:
                            logging.exception("Failed to process StatefulSet %s", sts_name)
        except ConnectionResetError as e:
            logging.info(e)
        logging.info("To avoid the ConnectionResetError. Timeout...")


if __name__ == "__main__":
    main()
