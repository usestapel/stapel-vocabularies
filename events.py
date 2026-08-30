"""comm event publisher for stapel-vocabularies.

One event: ``vocabulary.changed {slug, revision}``. It is what invalidates a
``CommResolver``'s cached ``describe`` and any downstream cache keyed on the
revision an ETag or a ``describe`` handed out.

Emitted through the transactional outbox, so the event leaves iff the load's
transaction commits — a fixture that half-loaded and rolled back must not tell
anybody its terms changed. Payload contract:
``schemas/emits/vocabulary.changed.json``.
"""


def publish_vocabulary_changed(slug, revision):
    """Emit ``vocabulary.changed`` for a loaded (or otherwise mutated) vocabulary.

    Called from inside the mutating transaction: ``mutate_and_emit`` joins it,
    so the event and the rows it describes commit together or not at all.
    Failures are deliberately not swallowed — core marks the transaction
    rollback-only on a failed emit, so a swallowed failure could not commit
    anyway, and a lost invalidation leaves every resolver cache stale.
    """
    from stapel_core.comm import mutate_and_emit

    # savepoint=False: joining the caller's transaction, there is nothing to
    # partially roll back to — a failed emit must sink the whole load.
    with mutate_and_emit(savepoint=False) as emit_event:
        emit_event(
            "vocabulary.changed",
            {"slug": str(slug), "revision": int(revision or 0)},
            key=str(slug),
        )


__all__ = ["publish_vocabulary_changed"]
