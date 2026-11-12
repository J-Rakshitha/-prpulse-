# Calls tasks.py so the same commands work where GNU make is missing.
.PHONY: init-db collect features train serve test

init-db:
	python tasks.py init-db

collect:
	python tasks.py collect

features:
	python tasks.py features

train:
	python tasks.py train

serve:
	python tasks.py serve

test:
	python tasks.py test
