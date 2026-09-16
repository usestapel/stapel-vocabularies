"""Every response body the contract declares is a body the views actually send.

``docs/schema.json`` is emitted from the views' ``@extend_schema``
annotations, and an annotation is a CLAIM: it says what the view returns, and
the generator has no way to check it against the method body.
``tests/test_contract.py`` compares the committed document against a FRESH
EMISSION of the same annotations — it proves the file is not stale, and
nothing else, because both sides come from the claim. stapel-alerts 0.2.0
shipped ``GET /issues`` declared as ``Issue[]`` while the wire carried
``{count, offset, limit, results}``: the drift gate was green and the
frontend pair rendered ``undefined``.

This is the gate the generator cannot be: it performs every operation the
committed schema declares with a JSON response body, and validates the body
it gets against the schema it was promised. It matters more here than in a
serializer-backed module: three of these four views build their body as a
plain ``dict`` inside a nested ``build()`` closure, with no serializer between
the annotation and the wire at all, so nothing but this file compares the two.

Rules this file holds itself to:

* an operation with a declared JSON response and no entry in ``RECIPES``
  FAILS LOUDLY — a gate that quietly covers three of four rows is the family
  of green that proves nothing;
* a path parameter the gate cannot fill fails at the point of substitution,
  naming the operation;
* the operations that genuinely cannot be driven in-process are listed by
  name in ``UNDRIVABLE`` with a one-line reason each. That list is asserted
  to be exactly current: a stale entry, or a missing reason, fails;
* a collection that comes back empty fails in the populated pass — an empty
  array validates against any item schema, and an empty ``{code: label}`` map
  validates against ``additionalProperties`` just as vacuously, so an empty
  answer is a check that looked at nothing;
* every read is driven a SECOND time in its emptiest legal state
  (``EMPTY_STATE``): a deployment holding no vocabularies, a vocabulary with
  no terms, a level nothing matches, a resolve whose codes are all unknown —
  AND, where the collection is what empties, a one-row branch where the row
  itself carries none of its optional values. Every null finding in the first
  wave of this gate was on an empty state.

Runs on every interpreter: it reads the committed schema and never emits.

THE MOUNT. ``codegen_urls.py`` mounts ``vocabularies/`` →
``stapel_vocabularies.urls``, which contributes ``api/v1/``. ``tests/urls.py``
mounts the identical prefix, so this module's suite has always been looking
where the document points — unlike five of the first eight libraries in this
wave, whose test urlconf pointed somewhere the document does not describe.
The emission mount is declared here rather than borrowed, so
``test_every_declared_path_resolves_under_this_urlconf`` fails at the one
moment it is cheap to fix: when somebody changes a mount.

What it found on its first run: 4 of 4 operations driven, 0 red. The claims
this module makes about its own wire are honest in both states, including
the two that are easiest to get wrong here:

* ``Level.parent`` is declared ``nullable`` and IS null for every root level
  — ``_serialize_vocabulary`` (views.py) emits ``level.get("parent")``, and a
  root level's fixture entry carries no ``parent`` key at all;
* ``Term.extra`` and ``Term.match`` are declared OPTIONAL (absent from
  ``required``) and the row builder omits them rather than sending ``{}`` /
  ``null`` — a term with no source attributes has no ``extra`` key, which is
  exactly what the document says.

``test_the_gate_is_not_blind`` proves that is a finding rather than a gate
that never looked: it re-validates every driven body against
``{"type": "string"}`` and requires all of them to fail.
"""
import copy
import json
import re
from pathlib import Path

import jsonschema
import pytest
from django.test import override_settings
from django.urls import include, path as url_path
from rest_framework.test import APIClient

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())

#: The mount the contract is emitted at, reproduced for the test client
#: (``codegen_urls.py``: ``vocabularies/`` → ``stapel_vocabularies.urls``,
#: which contributes ``api/v1/``).
urlpatterns = [
    url_path("vocabularies/", include("stapel_vocabularies.urls")),
]

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]

V1 = "/vocabularies/api/v1"


@pytest.fixture(autouse=True)
def _media_root(tmp_path):
    """Nothing here writes files today; pin the root so nothing ever does.

    ``MEDIA_ROOT`` is unset in this module's harness settings
    (``_codegen_settings.py``), so it defaults to the working directory — in
    stapel-auth that put a data export into the checkout, where under a flat
    package layout a stray directory also shadowed a real submodule.
    """
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# The contract side: what the document declares
# ─────────────────────────────────────────────────────────────────────────────


def _json_schema(node):
    """OpenAPI 3.0 → JSON Schema, for the divergence that matters here.

    OAS 3.0 spells "may be null" as ``nullable: true`` beside a ``type``;
    JSON Schema has no such keyword and would refuse the null — which is
    what ``Level.parent`` answers for every root level. Everything else
    drf-spectacular emits here (``$ref``, ``allOf``, ``enum``, ``required``,
    ``additionalProperties``) is JSON Schema as written.
    """
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(response_schema):
    root = copy.deepcopy(response_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    """Every ``(method, path, 2xx code, JSON body schema)`` the contract declares."""
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for code, response in op.get("responses", {}).items():
                body = (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


# ─────────────────────────────────────────────────────────────────────────────
# The wire side: harness
# ─────────────────────────────────────────────────────────────────────────────


def anonymous():
    """The caller this whole surface exists for: ``ReadOnlyOrStaff`` opens
    every read, and a session cookie would make the answers uncacheable."""
    return APIClient()


def load(fixture):
    """Apply one fixture through the real loader and hand back the row."""
    from stapel_vocabularies.loader import load_fixture
    from stapel_vocabularies.models import Vocabulary

    load_fixture(fixture, replace=True)
    return Vocabulary.objects.get(slug=fixture["slug"])


#: Fixture term rows are ``[level, code, label, external_id, sort,
#: popularity, extra]``, trailing entries optional (``loader._term_rows``).
def rich_vocabulary():
    """Two levels, a popular band, a source-owned ``extra`` bag and children.

    Everything optional in the ``Term`` shape is present on at least one row,
    so the populated pass has something to look at in every declared field.
    """
    return load(
        {
            "slug": "wire-makes",
            "name": "Wire Makes",
            "source": "wire-contract",
            "levels": [{"name": "Make"}, {"name": "Model", "parent": "Make"}],
            "terms": [
                # popularity > 0 -> the popular band leads the page
                ["Make", "alfa", "Alfa", None, 0, 9, {"hue": "#1a1a1a"}],
                ["Make", "bravo", "Bravo", None, 1, 5],
                # no popularity, no extra: the plain `all` row
                ["Make", "charlie", "Charlie", None, 2],
                ["Model", "alfa-one", "Alfa One", None, 0],
                ["Model", "alfa-two", "Alfa Two", None, 1],
            ],
            "edges": [
                ["Make", "alfa", "Model", "alfa-one"],
                ["Make", "alfa", "Model", "alfa-two"],
            ],
        }
    )


def bare_vocabulary():
    """One root level, one row carrying nothing optional, no children.

    The emptiest legal vocabulary that still has a term to render: no
    popularity (so ``band`` is ``all`` and ``popular_count`` is 0), no
    ``extra`` (so the key is absent), no edges (so ``has_children`` is
    false), and a root level whose ``parent`` is null.
    """
    return load(
        {
            "slug": "wire-bare",
            "name": "Wire Bare",
            "source": "wire-contract",
            "levels": [{"name": "Tint"}],
            "terms": [["Tint", "slate", "Slate", None, 0]],
        }
    )


def empty_vocabulary():
    """A vocabulary with a level and not one term in it.

    A catalogue that has been declared but not imported yet — the state a
    frontend sees between ``load_vocabulary`` being scheduled and it running.
    """
    return load(
        {
            "slug": "wire-empty",
            "name": "Wire Empty",
            "source": "wire-contract",
            "levels": [{"name": "Tint"}],
            "terms": [],
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# The recipe table
# ─────────────────────────────────────────────────────────────────────────────


class Call:
    """Performs one declared operation, and refuses to guess a path parameter."""

    def __init__(self, method, path):
        self.method = method
        self.path = path

    def __call__(self, client, params=None, data=None, query="", **extra):
        url = self.path
        for name, value in (params or {}).items():
            url = url.replace("{%s}" % name, str(value))
        assert "{" not in url, (
            f"{self.method} {self.path}: a path parameter this gate does not "
            "know how to fill — teach its recipe, or the operation goes unchecked"
        )
        send = getattr(client, self.method.lower())
        if self.method in ("GET", "DELETE"):
            return send(url + query, **extra)
        return send(url + query, data if data is not None else {}, format="json", **extra)


#: How to perform each operation the contract declares with a JSON response
#: body, keyed by ``(METHOD, path template)``. A recipe returns the response it
#: produced, or a list of ``(label, response)`` pairs when one operation has
#: more than one answering state worth asking.
RECIPES = {}

#: The same operations again, in the emptiest state the contract still has to
#: describe. Where the collection is what empties, the recipe drives BOTH the
#: genuinely empty collection and a one-row collection whose row carries none
#: of its optional values — an empty array proves nothing about the item
#: schema, so the second branch is the one that does the work.
EMPTY_STATE = {}


def recipe(method, path, table=None):
    def register(fn):
        target = RECIPES if table is None else table
        key = (method, V1 + path)
        assert key not in target, f"duplicate recipe for {method} {path}"
        target[key] = fn
        return fn

    return register


def empty_state(method, path):
    return recipe(method, path, table=EMPTY_STATE)


#: Operations that cannot be driven in-process, by name and with the reason.
#: A short, visible list is acceptable here; a silent skip is not.
#:
#: EMPTY. Every operation this module declares is an anonymous read over rows
#: a fixture can create, so there is nothing here to stand in for.
UNDRIVABLE: dict = {}

#: Collections nested inside an object body that must actually carry a row in
#: the populated pass. An empty array validates against any item schema, so a
#: populated run that leaves one empty looked at nothing — and the generic
#: "is the body a list" check cannot see a list one level down.
POPULATED_COLLECTIONS = {
    ("GET", V1 + "/vocabularies/{slug}/"): ("levels",),
    ("GET", V1 + "/vocabularies/{slug}/terms/"): ("results",),
}

#: Operations whose whole body is a free-form map. ``additionalProperties: {}``
#: accepts ``{}`` as happily as it accepts a full answer, so the populated
#: pass requires at least one entry or it validated nothing at all.
POPULATED_MAPS = {
    ("GET", V1 + "/vocabularies/{slug}/terms/resolve/"),
}


# ── the catalogue ────────────────────────────────────────────────────────────


@recipe("GET", "/vocabularies/")
def _vocabulary_list(call):
    rich_vocabulary()
    bare_vocabulary()
    return call(anonymous())


@empty_state("GET", "/vocabularies/")
def _vocabulary_list_empty(call):
    """A deployment that has imported nothing yet, and one that holds a
    single vocabulary with no terms in it — an empty ARRAY says nothing
    about the item schema, so the second branch is where the check lands."""
    empty = call(anonymous())
    empty_vocabulary()
    declared_but_unimported = call(anonymous())
    return [
        ("no vocabularies at all", empty),
        ("one vocabulary, no terms in it", declared_but_unimported),
    ]


@recipe("GET", "/vocabularies/{slug}/")
def _vocabulary_detail(call):
    vocabulary = rich_vocabulary()
    return call(anonymous(), params={"slug": vocabulary.slug})


@empty_state("GET", "/vocabularies/{slug}/")
def _vocabulary_detail_empty(call):
    """A single root level (``parent`` is null — the one nullable field in
    this shape) and ``term_count`` 0."""
    vocabulary = empty_vocabulary()
    return call(anonymous(), params={"slug": vocabulary.slug})


# ── the term listing ─────────────────────────────────────────────────────────


@recipe("GET", "/vocabularies/{slug}/terms/")
def _terms(call):
    """Three states of the same listing: a whole level (popular band leading,
    ``extra`` present, ``has_children`` true), the children of one parent,
    and a ``q`` search where the prefix rank outranks the band."""
    vocabulary = rich_vocabulary()
    client = anonymous()
    return [
        ("a whole level", call(client, params={"slug": vocabulary.slug}, query="?level=Make")),
        (
            "the children of one parent",
            call(
                client,
                params={"slug": vocabulary.slug},
                query="?level=Model&parent=alfa",
            ),
        ),
        (
            "a q search",
            call(client, params={"slug": vocabulary.slug}, query="?level=Make&q=al"),
        ),
    ]


@empty_state("GET", "/vocabularies/{slug}/terms/")
def _terms_empty(call):
    """Three kinds of nothing: a level with no terms, a search that matches
    none, and a level holding exactly one row that carries nothing optional
    (no popularity, no ``extra``, no children) — the branch that actually
    exercises the ``Term`` schema when the others cannot."""
    empty = empty_vocabulary()
    bare = bare_vocabulary()
    client = anonymous()
    return [
        (
            "a level with no terms",
            call(client, params={"slug": empty.slug}, query="?level=Tint"),
        ),
        (
            "a search that matches nothing",
            call(
                client,
                params={"slug": bare.slug},
                query="?level=Tint&q=nothing-matches-this",
            ),
        ),
        (
            "one row carrying nothing optional",
            call(client, params={"slug": bare.slug}, query="?level=Tint"),
        ),
    ]


# ── resolve ──────────────────────────────────────────────────────────────────


@recipe("GET", "/vocabularies/{slug}/terms/resolve/")
def _resolve(call):
    vocabulary = rich_vocabulary()
    return call(
        anonymous(),
        params={"slug": vocabulary.slug},
        query="?level=Make&codes=alfa,bravo",
    )


@empty_state("GET", "/vocabularies/{slug}/terms/resolve/")
def _resolve_empty(call):
    """Unknown codes are omitted rather than answered null, so the emptiest
    legal answer is ``{}`` — the caller falls back to the code, which is what
    a stored value does when its labels are missing."""
    vocabulary = rich_vocabulary()
    return call(
        anonymous(),
        params={"slug": vocabulary.slug},
        query="?level=Make&codes=no-such-code",
    )


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────


#: Operations whose declared body the wire does not send, with the defect and
#: its owner. ``strict=True``: a fixed entry fails until it is deleted, so a
#: finding can be neither forgotten nor quietly kept.
#:
#: EMPTY, and that is the finding rather than the absence of one: 4 of 4
#: operations answer the shape they declare, in both states. The mechanism
#: stays because the next wave will need it.
KNOWN_MISMATCHES: dict = {}


def test_the_contract_declares_something_to_check():
    assert OPERATIONS, "docs/schema.json declares no JSON responses at all"


def test_every_declared_path_resolves_under_this_urlconf():
    """The suite must be looking where the document describes.

    Five of the first eight libraries this gate was written for had a
    committed contract that nothing had ever driven, because the test urlconf
    mounted somewhere the document does not describe: one mounted a different
    prefix AND one segment short, one mounted the paths bare, one mounted a
    doubled segment, one mounted less than the emission did. In every case
    the operations were "covered" by a file that could not have reached a
    single one of them.

    Vocabularies is not one of them — ``tests/urls.py`` and
    ``codegen_urls.py`` mount the identical ``vocabularies/`` prefix — and
    this assertion is what keeps saying so. A missing recipe already fails
    loudly; this fails when the MOUNT is wrong, which no per-operation check
    can see, because when the mount is wrong every operation is equally and
    silently unreachable.
    """
    from django.urls import Resolver404, resolve

    # Resolution cares about the SHAPE of a segment, and a urlconf may use
    # several converters — uuid, int, slug. A path counts as reachable if any
    # one shape resolves: the question here is whether the mount exists, not
    # whether a particular id does.
    candidates = (
        "00000000-0000-4000-8000-000000000000",
        "1",
        "a-slug",
    )

    unreachable = []
    for _method, path, _code, _schema in OPERATIONS:
        for value in candidates:
            try:
                resolve(re.sub(r"\{[^}]+\}", value, path))
                break
            except Resolver404:
                continue
        else:
            unreachable.append(path)

    assert not unreachable, (
        "these declared paths do not resolve under this module's urlconf, so "
        "nothing here can be driving them — the mount is wrong, not the "
        "recipes:\n  " + "\n  ".join(sorted(set(unreachable)))
    )


def test_every_declared_operation_is_driven_or_named_undrivable():
    """No operation is covered by silence, and no entry outlives its operation."""
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    covered = set(RECIPES) | set(UNDRIVABLE)

    missing = sorted(declared - covered)
    assert not missing, (
        "operations with a declared JSON response body and no recipe:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    stale = sorted(covered - declared)
    assert not stale, (
        "recipes/exclusions for operations the contract no longer declares:\n"
        + "\n".join(f"  {m} {p}" for m, p in stale)
    )
    both = sorted(set(RECIPES) & set(UNDRIVABLE))
    assert not both, f"driven AND excluded: {both}"
    for key, reason in UNDRIVABLE.items():
        assert reason and reason.strip(), f"{key} is excluded with no reason"

    stale_expectations = sorted(
        (set(POPULATED_COLLECTIONS) | POPULATED_MAPS) - declared
    )
    assert not stale_expectations, (
        f"collection expectations for undeclared operations: {stale_expectations}"
    )


def test_every_read_is_also_driven_in_its_emptiest_state():
    """A populated answer cannot say what a field holds when there is nothing.

    Every null finding in the first wave of this gate was on the empty state.
    A gate that only ever seeds three rows and asks never sees any of them.
    """
    reads = {
        (method, path)
        for method, path, _code, _schema in OPERATIONS
        if method == "GET"
    }
    missing = sorted(reads - set(EMPTY_STATE))
    assert not missing, (
        "reads driven only against a populated database — the state where "
        "every null claim in this gate's history was found is unchecked:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    declared = {(m, p) for m, p, _c, _s in OPERATIONS}
    stale = sorted(set(EMPTY_STATE) - declared)
    assert not stale, f"empty-state recipes for undeclared operations: {stale}"


def test_every_known_mismatch_is_still_declared_and_explained():
    """A recorded defect must name a live operation and carry its reason.

    Without this, an operation that is renamed or removed leaves an entry that
    silences nothing and reads like a known problem forever.
    """
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    for key, reason in KNOWN_MISMATCHES.items():
        assert key in declared, (
            f"{key} is recorded as a known mismatch but the contract no longer "
            "declares it — delete the entry"
        )
        assert reason and reason.strip(), f"{key} is recorded with no reason"


def _labelled(result):
    """A recipe answers with one response, or with labelled branches."""
    if isinstance(result, list):
        return result
    return [("", result)]


def _drive(table, method, path, code, body_schema, *, expect_rows):
    perform = table.get((method, path))
    assert perform is not None, (
        f"{method} {path} declares a response body and has no recipe — an "
        "unchecked operation is a schema nobody proves. Teach RECIPES, or "
        "name it in UNDRIVABLE with a reason."
    )

    for label, response in _labelled(perform(Call(method, path))):
        where = f"{method} {path}" + (f" [{label}]" if label else "")
        assert response.status_code == code, (
            f"{where}: expected the declared {code}, got "
            f"{response.status_code}: {response.content[:400]}"
        )

        body = response.json()
        errors = sorted(
            _validator(body_schema).iter_errors(body), key=lambda e: list(e.path)
        )
        assert not errors, (
            f"{where} answers a body the contract does not describe:\n"
            + "\n".join(f"  at {list(e.path) or '<root>'}: {e.message}" for e in errors[:10])
            + f"\n  body: {json.dumps(body)[:600]}"
        )

        # An empty list validates against any item schema, and an empty map
        # against any additionalProperties, so a collection must actually
        # carry a row for the check to have looked at anything.
        if expect_rows:
            if isinstance(body, list):
                assert body, f"{where}: the declared list came back empty"
            if (method, path) in POPULATED_MAPS:
                assert isinstance(body, dict) and body, (
                    f"{where}: the declared map came back empty, so "
                    "additionalProperties validated nothing"
                )
            for name in POPULATED_COLLECTIONS.get((method, path), ()):
                assert isinstance(body, dict) and body.get(name), (
                    f"{where}: the declared collection {name!r} came back "
                    "empty, so nothing in it was checked"
                )


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    OPERATIONS,
    ids=[f"{m} {p}" for m, p, _c, _s in OPERATIONS],
)
def test_the_wire_matches_the_declared_response(method, path, code, body_schema, request):
    if (method, path) in UNDRIVABLE:
        pytest.skip(f"excluded by name: {UNDRIVABLE[(method, path)]}")

    if (method, path) in KNOWN_MISMATCHES:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{method} {path}: {KNOWN_MISMATCHES[(method, path)]}",
            )
        )

    _drive(RECIPES, method, path, code, body_schema, expect_rows=True)


_EMPTY_OPERATIONS = [
    (method, path, code, schema)
    for method, path, code, schema in OPERATIONS
    if (method, path) in EMPTY_STATE
]


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    _EMPTY_OPERATIONS,
    ids=[f"{m} {p}" for m, p, _c, _s in _EMPTY_OPERATIONS],
)
def test_the_wire_matches_the_declared_response_when_there_is_nothing_there(
    method, path, code, body_schema, request
):
    """The same claim, asked in the state where the nulls live."""
    if (method, path) in KNOWN_MISMATCHES:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{method} {path}: {KNOWN_MISMATCHES[(method, path)]}",
            )
        )

    _drive(EMPTY_STATE, method, path, code, body_schema, expect_rows=False)


def test_the_gate_is_not_blind():
    """A canary: swap a declared schema for one the wire cannot satisfy.

    Everything above can be green for two reasons — the claims are honest, or
    the check never looks at the body. This tells them apart by validating a
    real response against ``{"type": "string"}``: every operation here answers
    an object or an array, so every one of them must fail. If any passes, the
    validation in ``_drive`` is not reaching the received body and this whole
    file proves nothing. With ``KNOWN_MISMATCHES`` empty this covers the
    entire declared surface.
    """
    honest = [
        (method, path, code)
        for method, path, code, _schema in OPERATIONS
        if (method, path) not in KNOWN_MISMATCHES and (method, path) not in UNDRIVABLE
    ]
    assert honest, "nothing left to canary"

    survivors = []
    for method, path, code in honest:
        try:
            _drive(RECIPES, method, path, code, {"type": "string"}, expect_rows=False)
        except AssertionError:
            continue
        survivors.append(f"{method} {path}")
    assert not survivors, (
        "these operations passed validation against {'type': 'string'} — the "
        "gate is not looking at the body it received:\n  " + "\n  ".join(survivors)
    )
