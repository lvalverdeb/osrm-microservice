"""Models a deployment ships, not only the ones this repo does — `T-101`.

`servicemodel` calls a delivery model "an operation described as data". That is
only true if an operation this repository has never heard of can be described:
today `MODELS` is one directory resolved relative to the checkout, so the
capability stops at the repo boundary, and it stops entirely once `vrp` is
installed as a distribution rather than run from a tree.

So models are looked up along a *path*, nearest first, and a deployment
prepends its own directories through `VRP_MODEL_PATH`.

**This does not weaken `T-96`'s gate, and the distinction matters.** That gate
is this repository's rule about its own shipped set -- no model reaches
`models/` without passing it. A deployment that ships its own models owns the
same obligation and has the same tool: `modelcheck.check` is importable, and
running it is the consumer's job exactly as running it is ours. A search path
that could only be used unsafely would be a bad feature; one that hands over a
duty along with the capability is the honest shape.
"""

from __future__ import annotations

import json
import os

import pytest

from vrp import servicemodel


def write(directory, name: str, body: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(body))


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """A directory of models this repository does not ship."""
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    monkeypatch.setenv("VRP_MODEL_PATH", str(theirs))
    return theirs


def test_the_default_path_is_the_shipped_directory_alone(monkeypatch):
    """No deployment, no change: the repo behaves exactly as it did."""
    monkeypatch.delenv("VRP_MODEL_PATH", raising=False)

    assert servicemodel.model_path() == (servicemodel.MODELS,)


def test_a_model_the_deployment_ships_is_found(deployment):
    write(deployment, "night-courier", {"name": "night-courier"})

    assert servicemodel.model_for("night-courier")["name"] == "night-courier"


def test_the_nearer_directory_wins(deployment):
    """A deployment may override a shipped model, which is the point of an
    ordered path rather than a set."""
    write(deployment, "signed-envelopes", {"name": "signed-envelopes",
                                           "problem_id": "theirs"})

    assert servicemodel.model_for("signed-envelopes")["problem_id"] == "theirs"


def test_shipped_lists_every_directory_on_the_path(deployment):
    write(deployment, "night-courier", {"name": "night-courier"})
    listed = servicemodel.shipped()

    assert "night-courier" in listed
    assert "signed-envelopes" in listed, "the repo's own models must still list"
    assert listed == sorted(set(listed)), "a name in two directories lists once"


def test_an_unknown_model_names_what_the_whole_path_offers(deployment):
    """The refusal is the only place a reader learns what is available, so it
    must not describe one directory when two were searched."""
    write(deployment, "night-courier", {"name": "night-courier"})

    with pytest.raises(ValueError) as refusal:
        servicemodel.model_for("no-such-model")

    assert "night-courier" in str(refusal.value)
    assert "signed-envelopes" in str(refusal.value)


def test_a_fragment_is_found_along_the_path_too(deployment):
    """Composition is worth nothing to a deployment that cannot add fragments."""
    write(deployment / servicemodel.FRAGMENTS, "their-hours",
          {"shift": {"start": 0, "end": 3600}})

    assert servicemodel.fragment_for("their-hours")["shift"]["end"] == 3600


def test_the_path_is_read_when_it_is_used_not_when_imported(tmp_path, monkeypatch):
    """Set after import, and it still takes effect -- a process that configures
    itself in main() is the normal case, not the exception."""
    theirs = tmp_path / "late"
    write(theirs, "late-model", {"name": "late-model"})
    monkeypatch.setenv("VRP_MODEL_PATH", str(theirs))

    assert "late-model" in servicemodel.shipped()


def test_several_directories_are_searched_in_order(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    write(first, "shared", {"name": "shared", "problem_id": "first"})
    write(second, "shared", {"name": "shared", "problem_id": "second"})
    write(second, "only-second", {"name": "only-second"})
    monkeypatch.setenv("VRP_MODEL_PATH", os.pathsep.join([str(first), str(second)]))

    assert servicemodel.model_for("shared")["problem_id"] == "first"
    assert servicemodel.model_for("only-second")["name"] == "only-second"


def test_a_deployment_adds_categories_without_losing_ours(deployment):
    """Maps merge rather than the nearest file winning outright: adding one
    category must not cost you every category the repository already mapped."""
    write(deployment, "night-courier", {"name": "night-courier"})
    (deployment / "categories.json").write_text(json.dumps({"Legal": "night-courier"}))

    mapping = servicemodel.categories()
    assert mapping["Legal"] == "night-courier"
    assert mapping["Documents"] == "signed-envelopes"


def test_a_deployment_may_redirect_a_category_of_ours(deployment):
    """The other half of merging, and the half an ordering mistake breaks
    silently: nearest wins per key, so a redirect actually redirects."""
    write(deployment, "night-courier", {"name": "night-courier",
                                        "applies_to": ["Documents"]})
    (deployment / "categories.json").write_text(
        json.dumps({"Documents": "night-courier"}))

    assert servicemodel.categories()["Documents"] == "night-courier"
    assert servicemodel.model_for_category("Documents")["name"] == "night-courier"
