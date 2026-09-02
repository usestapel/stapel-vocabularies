"""Public reads over the vocabularies (spec §3.3).

The audience is a typeahead in somebody's listing composer and a cascading
select in a filter panel: anonymous, high-volume, cacheable. So:

* ``ReadOnlyOrStaff`` — reading is open, writing is not a thing this surface
  does at all (loading is ``manage.py load_vocabulary``, an operator action).
* ``ETag`` + ``Cache-Control`` derived from ``Vocabulary.revision``, because
  a vocabulary changes when a catalogue is re-imported and not otherwise. A
  client that comes back with ``If-None-Match`` gets a 304 and no query.
* ``Vary: Accept-Language`` — the labels in the body depend on it.
* No session, so no ``Set-Cookie``: a cookie would make every one of these
  responses uncacheable at the edge and start a session per crawler.
"""
import hashlib
import logging

from django.db.models import Case, IntegerField, Q, Value, When
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelErrorResponse, StapelResponse
from stapel_core.django.api.permissions import ReadOnlyOrStaff
from stapel_core.django.api.views import SerializerSeamMixin

from .conf import number, query_expander
from .errors import (
    ERR_400_BAD_PARENT,
    ERR_404_LEVEL_NOT_FOUND,
    ERR_404_VOCABULARY_NOT_FOUND,
)
from .models import Term, TermEdge, Vocabulary
from .serializers import TermPageSerializer, VocabularySerializer

logger = logging.getLogger(__name__)

#: Ordering under the optional prefix rank: the popular band first (by
#: descending popularity), then the level's own curated rank and its
#: alphabet. `popular_band` rather than `-popularity` alone so that a
#: hand-entered negative popularity means "not in the band" instead of
#: "below everything" — the column's contract is `0 = not in the band`, and
#: a signed column that is only ever read for its sign should say so.
BAND_ORDER = ("popular_band", "-popularity", "sort", "label")


def parse_accept_language(header):
    """Language tags from an ``Accept-Language`` header, best first.

    Only the ordering matters here — the caller walks the list and takes the
    first tag the term actually carries a label for, so a quality value that
    ties or a tag nobody translated into costs nothing. ``*`` is dropped: it
    means "anything", which is what the fallback label already is.
    """
    if not header:
        return []
    entries = []
    for index, part in enumerate(header.split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        tag = tag.strip()
        if not tag or tag == "*":
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        entries.append((-quality, index, tag))
    ordered = []
    for _, _, tag in sorted(entries):
        ordered.append(tag)
        base = tag.split("-")[0]
        if base != tag:
            ordered.append(base)
    return list(dict.fromkeys(ordered))


def pick_label(term_labels, label, languages):
    """The first translated label the client asked for, else ``label``."""
    if term_labels and languages:
        for language in languages:
            translated = term_labels.get(language)
            if translated:
                return translated
    return label


def _expand_query(query, languages):
    """The match variants of one typed query, literal first, deduplicated.

    The buyer who types «тимберленд» means the term labeled "Timberland" —
    and which scripts spell the same brand is the fleet's ONE normalization
    layer's knowledge, not this module's. So the query goes through the
    ``QUERY_EXPANDER`` seam (``expand.py``), in the same language the labels
    resolve for: the best ``Accept-Language`` tag, empty when the client
    states none.

    The literal query is prepended if the expander dropped it and the rest
    is deduplicated, so no expander can make the search narrower than the
    unexpanded one or OR the same pattern twice. An expander that blows up
    mid-keystroke costs recall, never the response: log, match literally.
    """
    language = languages[0] if languages else ""
    try:
        variants = list(query_expander()(query, language))
    except Exception:
        logger.warning(
            "STAPEL_VOCABULARIES['QUERY_EXPANDER'] raised on %r; matching "
            "the literal query only",
            query,
            exc_info=True,
        )
        return (query,)
    return tuple(dict.fromkeys([query, *filter(None, variants)]))


def _vector_function_name() -> str:
    from .conf import vocabularies_settings

    try:
        return str(vocabularies_settings.VECTOR_SIMILAR_FUNCTION or "")
    except AttributeError:
        return ""


def _band_annotation():
    """0 for a term in the popular band, 1 for the alphabet under it."""
    return Case(
        When(popularity__gt=0, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )


def _leading_popular(results) -> int:
    """How many rows at the HEAD of this page are in the popular band.

    Deliberately the leading run rather than a total: what the frontend
    needs is where to draw one separator, and the answer to that is
    "after the last row before the first non-popular one". It also stays
    correct under `q`, where the prefix rank outranks the band and there
    may be no leading run at all — 0, no separator, which is what a search
    result list should look like.
    """
    count = 0
    for row in results:
        if row["band"] != "popular":
            break
        count += 1
    return count


def _vector_rows(vocabulary, level, parent_id, query, languages, cap, taken_ids):
    """Rows the vector net adds under a thin deterministic answer.

    ``similar_labels`` (vector.py) asks the fleet's similarity Function —
    a no-op while ``VECTOR_SIMILAR_FUNCTION`` is empty, which is the
    default. What comes back is LABELS across every corpus the far side
    holds, so scope is re-imposed here: a label that is not a term of THIS
    vocabulary and level (and parent, in a cascade) is not a row, however
    much an embedding space likes it. Appended rows carry ``match:
    "vector"`` so a frontend can render "did you mean" differently from a
    literal hit; deterministic rows are untouched.
    """
    from .vector import similar_labels

    if cap <= 0:
        return []
    labels = similar_labels(query, languages[0] if languages else "", limit=cap)
    if not labels:
        return []
    terms = Term.objects.filter(vocabulary=vocabulary, level=level, label__in=labels)
    if parent_id is not None:
        terms = terms.filter(parent_edges__parent_id=parent_id)
    by_label = {}
    for row in terms.values("id", "code", "label", "labels"):
        by_label.setdefault(row["label"], row)
    picked = []
    for label in labels:
        row = by_label.get(label)
        if row is None or row["id"] in taken_ids:
            continue
        picked.append(row)
        if len(picked) >= cap:
            break
    if not picked:
        return []
    with_children = set(
        TermEdge.objects.filter(parent_id__in=[row["id"] for row in picked])
        .values_list("parent_id", flat=True)
        .distinct()
    )
    return [
        {
            "code": row["code"],
            "label": pick_label(row["labels"], row["label"], languages),
            "level": level,
            "has_children": row["id"] in with_children,
            # Always `all`, whatever the term's own popularity. These rows
            # are a "did you mean" appended BELOW the deterministic answer,
            # and a recommended band that only appears when the literal
            # search failed is not a recommended band.
            "band": "all",
            "match": "vector",
        }
        for row in picked
    ]


def _if_none_match(request, etag):
    header = request.META.get("HTTP_IF_NONE_MATCH", "")
    candidates = {
        value.strip().removeprefix("W/") for value in header.split(",") if value.strip()
    }
    return etag in candidates


def _conditional(request, tag_parts, build):
    """Answer the request, conditionally, and stamp the cache headers.

    The ETag is a digest of the identity of the ANSWER — the vocabulary's
    revision plus every parameter that shapes the body, the negotiated
    languages included — so it changes exactly when a re-import or a different
    query would change the bytes. A client that comes back with a matching
    ``If-None-Match`` gets a 304 and the body is never built, which is what
    makes the term listing cheap enough to sit behind a keystroke.
    """
    digest = hashlib.sha1(
        "|".join(str(part) for part in tag_parts).encode("utf-8")
    ).hexdigest()
    etag = f'"{digest}"'
    response = StapelResponse(status=304) if _if_none_match(request, etag) else build()
    response["ETag"] = etag
    response["Cache-Control"] = f"public, max-age={number('CACHE_MAX_AGE')}"
    response["Vary"] = "Accept-Language"
    return response


def _serialize_vocabulary(vocabulary):
    return {
        "slug": vocabulary.slug,
        "name": vocabulary.name,
        "levels": [
            {"name": level["name"], "parent": level.get("parent")}
            for level in vocabulary.levels or []
        ],
        "term_count": vocabulary.term_count,
        "revision": vocabulary.revision,
    }


class VocabularyListView(SerializerSeamMixin, APIView):
    """``GET vocabularies/`` — every vocabulary this deployment holds."""

    permission_classes = [ReadOnlyOrStaff]
    response_serializer_class = VocabularySerializer

    @extend_schema(
        summary="List vocabularies",
        description="Every vocabulary this deployment holds, with its levels "
        "and revision. Cacheable: the ETag covers the highest revision and "
        "the number of vocabularies.",
        responses={200: VocabularySerializer(many=True)},
    )
    def get(self, request):
        rows = list(Vocabulary.objects.all())
        highest = max((row.revision for row in rows), default=0)

        def build():
            return StapelResponse([_serialize_vocabulary(row) for row in rows])

        return _conditional(request, ("list", len(rows), highest), build)


class VocabularyDetailView(SerializerSeamMixin, APIView):
    """``GET vocabularies/{slug}/`` — one vocabulary."""

    permission_classes = [ReadOnlyOrStaff]
    response_serializer_class = VocabularySerializer

    @extend_schema(
        summary="Retrieve a vocabulary",
        responses={200: VocabularySerializer},
    )
    def get(self, request, slug):
        vocabulary = Vocabulary.objects.filter(slug=slug).first()
        if vocabulary is None:
            return StapelErrorResponse(404, ERR_404_VOCABULARY_NOT_FOUND)

        def build():
            return StapelResponse(_serialize_vocabulary(vocabulary))

        return _conditional(request, ("detail", slug, vocabulary.revision), build)


class TermListView(SerializerSeamMixin, APIView):
    """``GET vocabularies/{slug}/terms/`` — one page of one level."""

    permission_classes = [ReadOnlyOrStaff]
    response_serializer_class = TermPageSerializer

    @extend_schema(
        summary="Search the terms of one level",
        description=(
            "A page of terms at `level`, optionally the children of a `parent` "
            "term at the level above, optionally matching `q`. The query is "
            "expanded to match variants (cross-script, aliases) by the "
            "deployment's configured expander; a label starting with any "
            "variant ranks before the rest, then the popular band, then the "
            "level's own sort order and label. "
            "Rows lead with the **popular band** — the short recommended set "
            "a level opens on instead of whatever the alphabet put first. "
            "Each row carries `band` (`popular` / `all`) and the page carries "
            "`popular_count`, the number of leading rows in the band, so a "
            "control draws its separator without guessing. "
            "`has_children` is what tells a cascading control whether to ask "
            "for the next level. `total` counts the whole filtered set, before "
            "limit and offset."
        ),
        parameters=[
            OpenApiParameter("level", OpenApiTypes.STR, required=True,
                             description="Level to list. Required."),
            OpenApiParameter("parent", OpenApiTypes.STR,
                             description="Code of a term at the parent level; "
                                         "restricts the page to its children."),
            OpenApiParameter("q", OpenApiTypes.STR,
                             description="Case-insensitive substring of the "
                                         "label, matched against every "
                                         "variant the configured query "
                                         "expander returns (the literal "
                                         "query always among them)."),
            OpenApiParameter("limit", OpenApiTypes.INT,
                             description="Page size, 1..200 (default 50)."),
            OpenApiParameter("offset", OpenApiTypes.INT, description="Rows to skip."),
        ],
        responses={200: TermPageSerializer},
    )
    def get(self, request, slug):
        vocabulary = Vocabulary.objects.filter(slug=slug).first()
        if vocabulary is None:
            return StapelErrorResponse(404, ERR_404_VOCABULARY_NOT_FOUND)

        level = request.query_params.get("level") or ""
        if not vocabulary.has_level(level):
            return StapelErrorResponse(
                404,
                ERR_404_LEVEL_NOT_FOUND,
                params={"vocabulary": slug, "level": level},
            )

        parent_code = request.query_params.get("parent") or ""
        parent_id = None
        if parent_code:
            parent_level = vocabulary.parent_level(level)
            if parent_level:
                parent_id = (
                    Term.objects.filter(
                        vocabulary=vocabulary, level=parent_level, code=parent_code
                    )
                    .values_list("id", flat=True)
                    .first()
                )
            if parent_id is None:
                return StapelErrorResponse(
                    400,
                    ERR_400_BAD_PARENT,
                    params={"parent": parent_code, "level": level},
                )

        query = (request.query_params.get("q") or "").strip()
        limit = _bounded(
            request.query_params.get("limit"),
            default=number("DEFAULT_PAGE_SIZE"),
            maximum=number("MAX_PAGE_SIZE"),
        )
        offset = max(0, _integer(request.query_params.get("offset"), 0))
        languages = parse_accept_language(request.META.get("HTTP_ACCEPT_LANGUAGE"))
        variants = _expand_query(query, languages) if query else ()

        band_size = number("POPULAR_BAND_SIZE")

        def build():
            terms = Term.objects.filter(
                vocabulary=vocabulary, level=level
            ).annotate(popular_band=_band_annotation())
            if parent_id is not None:
                terms = terms.filter(parent_edges__parent_id=parent_id)
            if variants:
                contains = Q()
                prefix = Q()
                for variant in variants:
                    contains |= Q(label__icontains=variant)
                    prefix |= Q(label__istartswith=variant)
                terms = terms.filter(contains).annotate(
                    prefix_rank=Case(
                        When(prefix, then=Value(0)),
                        default=Value(1),
                        output_field=IntegerField(),
                    )
                ).order_by("prefix_rank", *BAND_ORDER)
            else:
                terms = terms.order_by(*BAND_ORDER)
            total = terms.count()
            page = list(
                terms.values("id", "code", "label", "labels", "popularity")[
                    offset:offset + limit
                ]
            )
            with_children = set(
                TermEdge.objects.filter(
                    parent_id__in=[row["id"] for row in page]
                )
                .values_list("parent_id", flat=True)
                .distinct()
            )
            results = [
                {
                    "code": row["code"],
                    "label": pick_label(row["labels"], row["label"], languages),
                    "level": level,
                    "has_children": row["id"] in with_children,
                    # The band ends at POPULAR_BAND_SIZE rows of the ANSWER,
                    # and popular rows lead the answer, so a row's position
                    # decides it — no second query, and a curated fixture
                    # that promoted forty terms still cannot hand a frontend
                    # a forty-row "recommended" band.
                    "band": (
                        "popular"
                        if row["popularity"] > 0 and offset + index < band_size
                        else "all"
                    ),
                }
                for index, row in enumerate(page)
            ]
            appended = []
            if query and offset == 0 and total < number("VECTOR_MIN_RESULTS"):
                appended = _vector_rows(
                    vocabulary,
                    level,
                    parent_id,
                    query,
                    languages,
                    limit - len(results),
                    {row["id"] for row in page},
                )
            return StapelResponse(
                # `total` counts what this page's filters matched plus what
                # the net added: it may never claim fewer rows than shown.
                {
                    "results": results + appended,
                    "total": total + len(appended),
                    "popular_count": _leading_popular(results),
                }
            )

        return _conditional(
            request,
            (
                "terms",
                slug,
                vocabulary.revision,
                level,
                parent_code,
                # The variants, not the raw query: swapping the expander
                # changes the body of the same request, so it must change
                # the ETag with it.
                "\x1f".join(variants),
                limit,
                offset,
                ",".join(languages),
                # Decides which rows are labelled `popular` and therefore
                # what `popular_count` is: a body-shaping parameter like
                # any other, so it belongs in the validator. The band
                # CONTENTS need no part here — promoting a term bumps the
                # vocabulary's revision, which is already in this digest.
                band_size,
                # Only when the vector net is on: toggling it must move the
                # ETag, while the off state keeps its exact prior ETags.
                *(
                    (str(vector_function),)
                    if (vector_function := _vector_function_name())
                    else ()
                ),
            ),
            build,
        )


class TermResolveView(SerializerSeamMixin, APIView):
    """``GET vocabularies/{slug}/terms/resolve/`` — codes to labels."""

    permission_classes = [ReadOnlyOrStaff]

    @extend_schema(
        summary="Resolve term codes to labels",
        description=(
            "`{code: label}` for the codes named in `codes` (comma separated, "
            "at most 200 — the rest are ignored). Unknown codes are omitted, "
            "so a caller falls back to the code, which is what a stored DAO "
            "value does when its labels are missing."
        ),
        parameters=[
            OpenApiParameter("level", OpenApiTypes.STR, required=True,
                             description="Level the codes belong to. Required."),
            OpenApiParameter("codes", OpenApiTypes.STR, required=True,
                             description="Comma-separated term codes, at most 200."),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, slug):
        vocabulary = Vocabulary.objects.filter(slug=slug).first()
        if vocabulary is None:
            return StapelErrorResponse(404, ERR_404_VOCABULARY_NOT_FOUND)

        level = request.query_params.get("level") or ""
        if not vocabulary.has_level(level):
            return StapelErrorResponse(
                404,
                ERR_404_LEVEL_NOT_FOUND,
                params={"vocabulary": slug, "level": level},
            )

        raw = request.query_params.get("codes") or ""
        codes = [code for code in (part.strip() for part in raw.split(",")) if code]
        codes = list(dict.fromkeys(codes))[: number("MAX_PAGE_SIZE")]
        languages = parse_accept_language(request.META.get("HTTP_ACCEPT_LANGUAGE"))

        def build():
            rows = Term.objects.filter(
                vocabulary=vocabulary, level=level, code__in=codes
            ).values("code", "label", "labels")
            return StapelResponse(
                {
                    row["code"]: pick_label(row["labels"], row["label"], languages)
                    for row in rows
                }
            )

        return _conditional(
            request,
            (
                "resolve",
                slug,
                vocabulary.revision,
                level,
                ",".join(codes),
                ",".join(languages),
            ),
            build,
        )


def _integer(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded(value, default, maximum):
    parsed = _integer(value, default)
    return max(1, min(parsed, maximum))


__all__ = [
    "TermListView",
    "TermResolveView",
    "VocabularyDetailView",
    "VocabularyListView",
    "parse_accept_language",
    "pick_label",
]
