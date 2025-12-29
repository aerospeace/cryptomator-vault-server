FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install dependencies for cryptomator-cli
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget openjdk-21-jre-headless curl ca-certificates fuse3 unzip 
    # && wget -O /usr/local/bin/cryptomator-cli.jar https://github.com/cryptomator/cli/releases/download/1.8.0/cryptomator-cli-1.8.0.jar \
    # && chmod +x /usr/local/bin/cryptomator-cli.jar \

# Install Cryptomator CLI
ARG CRYPTOMATOR_VERSION=0.6.2
RUN curl -fsSL \
        https://github.com/cryptomator/cli/releases/download/${CRYPTOMATOR_VERSION}/cryptomator-cli-${CRYPTOMATOR_VERSION}-linux-x64.zip \
        -o /tmp/cryptomator-cli.zip \
    &&  unzip /tmp/cryptomator-cli.zip -d /opt \
    && ln -s /opt/cryptomator-cli/bin/cryptomator-cli /usr/bin/cryptomator-cli 

# Cleanup
RUN rm -rf /tmp/cryptomator-cli.zip \
    && apt-get purge -y unzip \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Verify installation
RUN test -f /usr/bin/cryptomator-cli && echo "cryptomator-cli exists" || (echo "cryptomator-cli missing" && exit 1)

# Bolerplate for runtime, should probably be grouped with python setup
COPY app ./app

ENV FLASK_APP=app.main:create_app
ENV PYTHONUNBUFFERED=1

ENV ADAPTER=cli
# ENV CRYPTOMATOR_CLI_PATH=/usr/local/bin/cryptomator-cli

EXPOSE 8000



# Root permissions needed for fusermount and user creation
RUN chmod u+s /bin/umount
# RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
# If override the use, needs probably to ensure the user exists in the container
RUN mkdir -p /var/www && chown 33:33 /var/www
USER 33:33


CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app.main:create_app()"]
