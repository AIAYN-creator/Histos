import pytest

from histos import canvas, frontmatter, operations


def _add_card(vault_root, card_id, *, description=None, depends_on=None):
    """Minimal add-card equivalent built directly on canvas/frontmatter, not the CLI --
    keeps these tests independent of cli.py, matching the layering the refactor
    introduces (canvas/frontmatter/validation <- operations <- cli).
    """
    data = canvas.load(vault_root)
    depends_on = depends_on or []
    all_approved = all(canvas.find_card(data, d)["color"] == canvas.APROBADA for d in depends_on)
    color = canvas.BACKLOG if (not depends_on or all_approved) else canvas.BLOQUEADA
    width, height = canvas.estimate_card_size(description)
    node = canvas.add_card_node(data, card_id, color, width=width, height=height)
    for dep in depends_on:
        canvas.add_edge(data, dep, card_id)
    canvas.place_new_card(data, node)
    meta = dict(frontmatter.DEFAULT_META)
    if description:
        meta["description"] = description
    frontmatter.write(canvas.card_file_path(vault_root, node), meta, f"# {card_id}\n\n")
    canvas.save(vault_root, data)


def _assign(vault_root, card_id):
    data = canvas.load(vault_root)
    canvas.find_card(data, card_id)["color"] = canvas.EN_PROGRESO
    canvas.save(vault_root, data)


def _propose(vault_root, card_id, body):
    data = canvas.load(vault_root)
    card = canvas.find_card(data, card_id)
    proposal_path = operations._proposal_path(vault_root, card_id)
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(body, encoding="utf-8")
    card["color"] = canvas.PROPUESTA_PENDIENTE
    canvas.save(vault_root, data)


def test_init_vault_creates_expected_structure(tmp_path):
    result = operations.init_vault(tmp_path)
    assert result.vault_root == tmp_path
    assert result.template_warnings == []
    assert canvas.vault_canvas_path(tmp_path).exists()
    assert (tmp_path / "content").is_dir()
    assert (tmp_path / "proposals").is_dir()
    assert (tmp_path / "approved").is_dir()
    assert (tmp_path / "AGENTS.md").exists()


def test_init_vault_raises_if_already_initialized(tmp_path):
    operations.init_vault(tmp_path)
    with pytest.raises(canvas.HistosError):
        operations.init_vault(tmp_path)


def test_get_status_groups_cards_by_state_in_order(initialized_vault):
    _add_card(initialized_vault, "cap1", description="intro")
    _add_card(initialized_vault, "cap2", depends_on=["cap1"])  # Blocked: cap1 isn't Approved yet

    result = operations.get_status(initialized_vault)
    assert [g.label for g in result.groups] == [
        "Backlog", "Blocked", "In progress",
        "Proposal pending review", "Dependency change request", "Approved",
    ]

    backlog = next(g for g in result.groups if g.color == canvas.BACKLOG)
    blocked = next(g for g in result.groups if g.color == canvas.BLOQUEADA)
    assert [c.id for c in backlog.cards] == ["cap1"]
    assert backlog.cards[0].description == "intro"
    assert [c.id for c in blocked.cards] == ["cap2"]
    assert blocked.cards[0].description is None


def test_get_diff_returns_current_and_proposed_bodies(initialized_vault):
    _add_card(initialized_vault, "cap1")
    _assign(initialized_vault, "cap1")
    _propose(initialized_vault, "cap1", "proposed text\n")

    result = operations.get_diff(initialized_vault, "cap1")
    assert result.card_id == "cap1"
    assert result.current_body == "# cap1\n\n"
    assert result.proposed_body == "proposed text\n"


def test_get_diff_raises_when_card_does_not_exist(initialized_vault):
    with pytest.raises(canvas.HistosError):
        operations.get_diff(initialized_vault, "nope")


def test_get_diff_raises_when_no_pending_proposal(initialized_vault):
    _add_card(initialized_vault, "cap1")
    with pytest.raises(canvas.HistosError):
        operations.get_diff(initialized_vault, "cap1")


def test_approve_writes_content_and_archives_proposal(initialized_vault):
    _add_card(initialized_vault, "cap1")
    _assign(initialized_vault, "cap1")
    _propose(initialized_vault, "cap1", "final text\n")

    result = operations.approve(initialized_vault, "cap1")
    assert result.card_id == "cap1"

    data = canvas.load(initialized_vault)
    assert canvas.find_card(data, "cap1")["color"] == canvas.APROBADA
    _, body = frontmatter.read(initialized_vault / "content" / "cap1.md")
    assert body == "final text\n"
    assert (initialized_vault / "approved" / "cap1.md").exists()
    assert not (initialized_vault / "proposals" / "cap1.md").exists()


def test_approve_raises_when_card_does_not_exist(initialized_vault):
    with pytest.raises(canvas.HistosError):
        operations.approve(initialized_vault, "nope")


def test_approve_raises_when_no_pending_proposal(initialized_vault):
    _add_card(initialized_vault, "cap1")
    with pytest.raises(canvas.HistosError):
        operations.approve(initialized_vault, "cap1")


def test_reject_discards_proposal_and_records_feedback(initialized_vault):
    _add_card(initialized_vault, "cap1")
    _assign(initialized_vault, "cap1")
    _propose(initialized_vault, "cap1", "draft\n")

    result = operations.reject(initialized_vault, "cap1", feedback="needs more detail")
    assert result.card_id == "cap1"

    data = canvas.load(initialized_vault)
    assert canvas.find_card(data, "cap1")["color"] == canvas.BACKLOG
    assert not (initialized_vault / "proposals" / "cap1.md").exists()
    meta, _ = frontmatter.read(initialized_vault / "content" / "cap1.md")
    assert meta["status_note"] == "needs more detail"


def test_reject_raises_when_card_does_not_exist(initialized_vault):
    with pytest.raises(canvas.HistosError):
        operations.reject(initialized_vault, "nope")


def test_reject_raises_when_no_pending_proposal(initialized_vault):
    _add_card(initialized_vault, "cap1")
    with pytest.raises(canvas.HistosError):
        operations.reject(initialized_vault, "cap1")
