# Third-Party Notices

## PydanticAI

- Project: https://github.com/pydantic/pydantic-ai
- License: MIT
- Usage: Agent tool-calling loop, typed tools, streaming node events,
  message history and deferred tool confirmation.
- Integration: Used as a Python dependency. No PydanticAI brand assets,
  website content or product UI are copied.

The project must retain PydanticAI's license through the installed Python
package and dependency metadata.

## Lobe Icons

- Project: https://github.com/lobehub/lobe-icons
- Package: `@lobehub/icons-static-svg` 1.94.0
- License: MIT (individual brand marks remain subject to their owners' terms)
- Usage: Local, offline Provider/model brand glyphs in the Obsidian model picker.
- Integration: A bounded set of static SVG assets is installed with the plugin.
  No remote CDN or runtime tracking request is used.

## Claudian (design reference)

- Project: https://github.com/YishenTu/claudian
- Reviewed revision: ec6b6f7e69879e126c93994749bbd6a3fec82858
- License: MIT (Copyright (c) 2025)
- Usage: Provider-neutral runtime contracts, ordered stream content, inline
  confirmation lifecycle, and architecture-boundary testing were studied as
  design references.
- Integration: Zhixu uses an independently implemented, restricted HTTP/NDJSON
  adapter for its sole PydanticAI backend. No Claudian CLI process management,
  unrestricted file tools, provider implementations, or source files are
  copied into this repository.
