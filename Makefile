.PHONY: bootstrap doctor config test up down logs

bootstrap:
	./scripts/bootstrap_docker_project.sh

doctor:
	./scripts/docker_doctor.sh

config:
	docker compose config

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f
