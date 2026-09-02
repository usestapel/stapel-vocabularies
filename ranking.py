"""Deriving the popular band from what a deployment actually sells.

A curated band ages. Somebody ranks twelve phone brands by hand, the market
moves, and eighteen months later the shortcut past the alphabet is pointing
at last year's alphabet. The rank that does not age is the one the listings
themselves state: whichever vendor codes the most live listings carry are
the vendor codes the next seller is most likely to want.

This module does NOT hold those counts and must not learn to. The listing
table belongs to a host service (a classified, a shop), which is also the
only place that knows what "live" means there — published, not expired, in
this region, in this locale. So the contract is a push, not a pull:
:func:`apply_popularity` takes an already-counted ``{code: count}`` map and
turns it into a band. Over the bus that same call is the
``vocabularies.set_popularity`` Function.

Two properties are load-bearing and both are tested:

* **Idempotent.** A host running this nightly against unchanged counts must
  write nothing, bump no revision and emit no invalidation — otherwise every
  ETag in front of the term listing dies once a night for nothing.
* **Bounded in queries.** One UPDATE for the band (a ``CASE`` over at most
  ``POPULAR_BAND_SIZE`` codes) and one for the demotions. A loop of saves
  over a 15 000-term level would make a nightly job an outage.

A push that DOES change something bumps ``Vocabulary.revision`` and emits
``vocabulary.changed``, exactly as a fixture load does: the order of every
term page just changed, and the revision is the one cache key this module
hands out.
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional, Union

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When

from .conf import number
from .events import publish_vocabulary_changed
from .models import Term, Vocabulary


def _vocabulary(vocabulary: Union[Vocabulary, str]) -> Optional[Vocabulary]:
    """Accept the row or its slug — the comm Function only has the slug."""
    if isinstance(vocabulary, Vocabulary):
        return vocabulary
    return Vocabulary.objects.filter(slug=str(vocabulary)).first()


def _band(counts: Mapping[str, int], band_size: int) -> Dict[str, int]:
    """``{code: popularity}`` for the top *band_size* codes, highest first.

    Sorted by ``(-count, code)``: the code breaks the tie so that two runs
    over the same counts produce the same band, which is what makes the
    whole call idempotent. The popularity VALUES are ranks, not counts —
    ``band_size`` down to 1 — because a count is a number that changes every
    hour, and writing it into the column would make every nightly push a
    full-table update and a fresh revision for an order nobody noticed
    moving.
    """
    ranked = sorted(
        ((str(code), int(count)) for code, count in (counts or {}).items()),
        key=lambda pair: (-pair[1], pair[0]),
    )[:band_size]
    return {code: len(ranked) - index for index, (code, _) in enumerate(ranked)}


@transaction.atomic
def apply_popularity(
    vocabulary: Union[Vocabulary, str],
    level: str,
    counts: Mapping[str, int],
    *,
    band_size: Optional[int] = None,
) -> int:
    """Rank one level from observed counts. Returns how many terms were ranked.

    *counts* is ``{term code: observed count}`` — as many codes as the host
    likes; only the top ``band_size`` (default ``POPULAR_BAND_SIZE``) enter
    the band, and **every other term at that level is demoted to 0**. That
    second half is the point: a band nothing ever leaves is a curated band
    with extra steps, and a vendor that stopped selling has to be able to
    fall out of the shortcut on its own.

    Codes that name no term at this level are ignored rather than refused —
    a host counting its listings will legitimately hold codes from a
    catalogue revision this deployment has not imported yet, and refusing
    the whole push over one of them would freeze the band on the old data.
    They are simply not counted in the return value, which is therefore
    "how many terms this actually ranked", not "how many codes you sent".
    """
    row = _vocabulary(vocabulary)
    if row is None:
        return 0
    size = band_size if band_size is not None else number("POPULAR_BAND_SIZE")
    wanted = _band(counts, max(0, int(size)))

    terms = Term.objects.filter(vocabulary=row, level=level)
    # One query establishes both which of the requested codes are live terms
    # here and what they already hold — so a no-op push can be recognised as
    # one without reading the level.
    current = dict(
        terms.filter(code__in=list(wanted)).values_list("code", "popularity")
    )
    desired = {code: rank for code, rank in wanted.items() if code in current}
    moved = {code: rank for code, rank in desired.items() if current[code] != rank}

    if moved:
        terms.filter(code__in=list(moved)).update(
            popularity=Case(
                *[When(code=code, then=Value(rank)) for code, rank in moved.items()],
                default=Value(0),
                output_field=IntegerField(),
            )
        )
    # `.exclude(popularity=0)` is not an optimisation: it makes the rowcount
    # mean "terms that actually left the band", which is what decides whether
    # anything downstream needs invalidating.
    demoted = (
        terms.exclude(code__in=list(desired)).exclude(popularity=0).update(popularity=0)
    )

    if moved or demoted:
        # The order of every page of this level just changed, and the ETag,
        # both resolvers' describe cache and any client-side copy are all
        # keyed on the revision. Same mechanism a fixture load uses; the
        # event leaves iff this transaction commits.
        row.save(update_fields=["revision"])
        row.refresh_from_db(fields=["revision"])
        publish_vocabulary_changed(row.slug, row.revision)

    return len(desired)


__all__ = ["apply_popularity"]
