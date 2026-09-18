FROM python:3.9

LABEL maintainer="Peng"

WORKDIR /app
# RUN pip3 install pip3 -U

COPY requirements.txt /app/requirements.txt
RUN pip3 install -r requirements.txt
# ADD //wsl$/Ubuntu-18.04/etc/ssl /certs/West-CA1.pem /etc/ssl/certs/west-ca.pem
# RUN update-ca-certificates
COPY main-resize-operator/resize_sts_operator.py /app/resize_sts_operator.py

CMD ["python3", "resize_sts_operator.py"]
