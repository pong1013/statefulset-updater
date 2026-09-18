import json
import ssl
import sys
import logging
import requests
from flask import Flask, request, abort
from kubernetes import client, config, watch
import base64

try:
    # local load kubeconfig
    config.load_kube_config("PATH TO KUBECONFIG")
except:
    # load_kube_config throws if there is no config, but does not document what it throws, so I can't rely on any particular type here
    config.load_incluster_config()

v1 = client.CoreV1Api()
api = client.AppsV1Api()


# 告訴Flask我們現在要執行一個叫做app的網站程式，__name__是Flask內的模組名稱
app = Flask(__name__)
app.debug = False

# configure log
app.logger.addHandler(
    logging.StreamHandler(sys.stdout)
)  # write log to the standard output stream
app.logger.setLevel(logging.INFO)

VOLUME_UPPER_BOUND = 2000  # Gi upper bound


@app.route("/mutating", methods=["POST"])  # 描述app網站程式的路徑，"/"表示是首頁
def mutate_statefulset():  # POST endpoint進行傳送 用來將mutate_statefulset與URL path '/' 關聯起來

    admission_request = request.get_json()
    app.logger.info("Received admission request %s", admission_request["request"]["uid"])
    # Extract the StatefulSet object from the admission request
    request_object = admission_request["request"]["object"]

    # check if the statefulset has volumeClaimTemplates
    try:
        volume_claim_templates = request_object["spec"]["volumeClaimTemplates"]
        num_volume_claims = len(volume_claim_templates)
        app.logger.info(num_volume_claims)

    except:
        return admission_response(True, admission_request)

    patch_list = []
    annotations_created = False

    for vc in range(num_volume_claims):
        vc = int(vc)
        new_storage_size = volume_claim_templates[vc]["spec"]["resources"]["requests"][
            "storage"
        ]
        # Load previous storage size from an external database or file
        try:
            namespace = request_object["metadata"]["namespace"]
            name = request_object["metadata"]["name"]
            statefulset = api.read_namespaced_stateful_set(name, namespace)
            previous_storage_size = statefulset.spec.volume_claim_templates[
                vc
            ].spec.resources.requests["storage"]
        except:
            return admission_response(True, admission_request)

        # Transcript str type into int
        new_size_int = transcript(new_storage_size)
        previous_size_int = transcript(previous_storage_size)

        if new_size_int == previous_size_int:
            patch_list = patch_list
        else:
            if new_size_int > VOLUME_UPPER_BOUND:
                abort(400, "Storage size can only be modified by up to 2000 Gi(2Ti)")
            else:
                if new_size_int > previous_size_int:
                    app.logger.info("new size: %s", new_storage_size)
                    app.logger.info("previous size: %s", previous_storage_size)
                    if (
                        request_object["metadata"].get("annotations") is None
                        and not annotations_created
                    ):
                        patch_list.append(
                            {"op": "add", "path": "/metadata/annotations", "value": {}}
                        )
                        annotations_created = True
                    patch_list += [
                        {
                            "op": "replace",
                            "path": f"/spec/volumeClaimTemplates/{vc}/spec/resources/requests/storage",
                            "value": previous_storage_size,
                        },
                        {
                            "op": "add",
                            "path": f"/metadata/annotations/resize-statefulset-operator~1resize-{vc}",
                            "value": new_storage_size,
                        },
                    ]  # list

                elif new_size_int < previous_size_int:
                    # volume_claim_templates[0]['spec']['resources']['requests']['storage'] = previous_storage_size
                    abort(400, "New storage size must be greater than previous size")

    patch_size_json = json.dumps(patch_list)  # str
    encoded_patch = base64.b64encode(patch_size_json.encode("utf-8")).decode("utf-8")
    return admission_resize_response(True, admission_request, encoded_patch)


def admission_response(allowed, object):
    return json.dumps(
        {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "allowed": allowed,
                "uid": object["request"]["uid"],
            },
        }
    )


def admission_resize_response(allowed, object, patch):
    return json.dumps(
        {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "allowed": allowed,
                "uid": object["request"]["uid"],
                "patchType": "JSONPatch",
                "patch": patch,
            },
        }
    )


def transcript(storage_size):
    if storage_size[-2:] == "Ti":
        size_Ti = storage_size.replace("Ti", "")
        size_int = int(size_Ti) * 1000
    elif storage_size[-2:] == "Gi":
        size_Ti = storage_size.replace("Gi", "")
        size_int = int(size_Ti)
    return size_int


if __name__ == "__main__":
    app.run(
        host="0.0.0.0", port=443, ssl_context=("/certs/server.crt", "/certs/server.key")
    )  # 設定若__name__這個模組被指定為主要網站模組時，則啟動這app這個網站，執行app中的功能
