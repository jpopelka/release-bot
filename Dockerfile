FROM registry.fedoraproject.org/fedora:30

ENV LANG=en_US.UTF-8 \
    ANSIBLE_STDOUT_CALLBACK=debug \
    USER=release-bot \
    HOME=/home/release-bot

# Ansible doesn't like /tmp
COPY files/ /src/files/

# Install packages first and reuse the cache as much as possible
RUN dnf install -y ansible \
    && cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/install-rpm-packages.yaml \
    && dnf clean all

COPY setup.py setup.cfg files/recipe.yaml /src/
# setuptools-scm
COPY .git/ /src/.git/
COPY release_bot/ /src/release_bot/

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/recipe.yaml

CMD ["/usr/bin/run.sh"]
