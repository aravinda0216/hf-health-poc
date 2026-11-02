"""Blueprint-defined entities and APIs.

A blueprint can declare new entities (fields, types, references, counts) in data_spec.custom_entities and new endpoints in api_spec.custom_endpoints.
The data engine turns the entities into tables and deterministic records; the API engine turns the endpoints into live routes, OpenAPI, agent tools,
generated contract tests and smoke scenarios. No Python is written per entity. See spec.py for the schema.
"""
from .spec import CustomSpec, parse_spec, SpecError          # noqa: F401
