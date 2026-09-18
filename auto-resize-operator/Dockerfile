FROM python:3.9

LABEL maintainer="Peng"

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip3 install -r requirements.txt

COPY auto-resize-operator/auto.py /app/auto.py

CMD ["python3", "auto.py"]
