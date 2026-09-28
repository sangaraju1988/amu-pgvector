from amu_pgvector.embeddings import fake_embedder


def test_fake_embedder_is_deterministic():
    embed = fake_embedder(dim=16)
    assert embed("hello world") == embed("hello world")


def test_fake_embedder_distinguishes_text():
    embed = fake_embedder(dim=16)
    assert embed("hello world") != embed("goodbye world")


def test_fake_embedder_respects_dimension():
    embed = fake_embedder(dim=64)
    vec = embed("anything")
    assert len(vec) == 64
    assert all(-1.0 <= v <= 1.0 for v in vec)
