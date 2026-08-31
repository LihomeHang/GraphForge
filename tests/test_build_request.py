from app.models.api import BuildRequest


def test_build_request_defaults_to_strict_ontology_and_replacement():
    request = BuildRequest()

    assert request.ontology_mode == "strict"
    assert request.replace_existing is True
    assert request.documents_are_chunks is False


def test_build_request_accepts_mirofish_compatibility_options():
    request = BuildRequest(
        ontology_mode="soft",
        replace_existing=True,
        documents_are_chunks=True,
    )

    assert request.ontology_mode == "soft"
    assert request.documents_are_chunks is True
