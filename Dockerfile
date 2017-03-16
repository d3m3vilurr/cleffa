FROM library/python:2.7-slim

WORKDIR /opt

RUN set -x \
    && cd /etc/apt \
    && sed -i 's/deb.debian.org/ftp.kaist.ac.kr/g' sources.list \
    && sed -i 's/security.debian.org/ftp.kaist.ac.kr\/debian-security/g' sources.list \
    && builds='build-essential curl libssl-dev' \
    && apt-get update && apt-get install -y $builds --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && cd /opt \
    && curl -L https://github.com/d3m3vilurr/cleffa/raw/master/requirement.txt -o requirement.txt \
    && pip install -r requirement.txt \
    && apt-get purge -y --auto-remove build-essential

COPY bot.py /opt/bot.py

CMD ["python", "bot.py"]
