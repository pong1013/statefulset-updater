FROM python:3.13.18-slim-trixie@sha256:8a7c3cd378e4e6dfa5d0ea4f6ca1a11068c951e3de3146ebc484eadf4b05a2be

LABEL maintainer="Peng"

WORKDIR /app
# RUN pip3 install pip3 -U

COPY requirements.txt /app/requirements.txt
COPY requirements-build.txt /tmp/requirements-build.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-build.txt \
    && python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && python -m pip check \
    && python -m pip uninstall -y wheel packaging \
    && python -m pip check \
    && python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip /tmp/requirements-build.txt
# ADD //wsl$/Ubuntu-18.04/etc/ssl /certs/West-CA1.pem /etc/ssl/certs/west-ca.pem
# RUN update-ca-certificates
COPY main-resize-operator/resize_sts_operator.py /app/resize_sts_operator.py

CMD ["python3", "resize_sts_operator.py"]
