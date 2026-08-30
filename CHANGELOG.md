# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [6.7.1] - 2026-08-30

Patch release. No public API was removed and no signature became stricter;
6.7.1 is a drop-in replacement for 6.7.0.

### Fixed

- `typing.get_type_hints()` raised `NameError: name 'CancellationToken' is not
  defined` on `Mediator.send`, `send_async`, `create_stream`, `publish`,
  `publish_async` and on the `ISender` / `IPublisher` contracts. When 6.7.0
  split the single-file module into submodules, `mediator.py` kept the
  `"CancellationToken"` annotations but lost the import that resolves them.
  This broke any consumer that inspects annotations at runtime — dependency
  injection containers, FastAPI, pydantic, and documentation generators.
  `inspect.signature()` was unaffected, which is why the regression escaped
  review: it carries string annotations without evaluating them.
- `get_type_hints(Mediator.create_scope)` failed the same way on the
  `"ServiceContainer"` annotation. Importing the real class would create a
  cycle, since `di` already imports `mediator`, so the parameter is now typed
  against a local `Protocol` describing the one method the method actually
  needs.
- `CancellationToken.none` was annotated as an instance attribute; it is a
  `ClassVar`.
- Cancellation error messages no longer assume a token always has a source.
- Coroutine cleanup on the discarded-result paths narrowed from
  `inspect.isawaitable()` to `inspect.iscoroutine()`. Only coroutine objects
  provide `close()`, so the previous check could raise `AttributeError` on a
  `Future` or `Task`.

### Added

- `tests/test_v671.py` walks every callable reachable through the public API
  and asserts that `get_type_hints()` resolves for each one, so a missing
  import can no longer hide behind a passing signature check.
- The type checker now runs in CI.

### Changed

- `IRequestPreProcessor.process` and `IRequestPostProcessor.process` are
  annotated as returning `Awaitable[None] | None`. Async processors were
  already supported at runtime; the annotation was simply wrong.
- Removed several hundred unused imports left behind by the 6.7.0 module split.
  As a side effect, names that were only ever incidentally reachable through
  `py_mediatR.mediator` — including `handler`, `behavior`, `os`, `sys`, `json`,
  `Path`, `Enum`, `Generic`, `TypeVar` and `get_type_hints` — are no longer
  present on that submodule. The supported import path is unaffected:
  `py_mediatR.__all__` still exports the same 52 names, and
  `from py_mediatR import handler, behavior` continues to work. Only code
  reaching into the private submodule, as in
  `from py_mediatR.mediator import handler`, needs to change.

## [6.7.0] - 2026-08-30

### Added

- Call-chain tracing via `mediator.trace()`.
- `Optional[T]` autowiring in the service container.
- `py.typed` marker; the package ships type information (PEP 561).

### Changed

- The single-file implementation was split into submodules under
  `src/py_mediatR/`. `py_mediatR.py` remains as a compatibility facade.
- Captive dependencies are now rejected instead of silently accepted.

### Fixed

- Transaction cleanup and the sync-over-async bridge no longer fail silently.

[6.7.1]: https://github.com/AyhanOztemel/py_mediatR/releases/tag/v6.7.1
[6.7.0]: https://github.com/AyhanOztemel/py_mediatR/releases/tag/v6.7.0
