.PHONY: test lint deb clean

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

lint:
	shellcheck packaging/build-deb.sh packaging/debian/postinst packaging/debian/prerm packaging/debian/postrm
	@command -v ansible-playbook >/dev/null || { echo "ansible not installed, skipping"; exit 0; }; cd ansible && ANSIBLE_ROLES_PATH=$$PWD/roles ansible-playbook --syntax-check -i inventory.example.ini playbooks/site.yml

deb:
	packaging/build-deb.sh

clean:
	rm -rf dist
