# Third-Party Notices

## Pi Agent Core and Pi AI

- Project: https://github.com/earendil-works/pi
- Reviewed revision: `3da591ab74ab9ab407e72ed882600b2c851fae21`
- Packages: `@earendil-works/pi-agent-core` 0.80.10 and `@earendil-works/pi-ai` 0.80.10
- License: MIT
- Usage: Agent loop, typed model/tool protocol, streaming lifecycle, Steering,
  Follow-up and provider-neutral message types.
- Integration: the packages are bundled into the Obsidian plugin. Zhixu adds
  its own secure model transport, restricted tool adapter, task authorization,
  persistence, compaction and policy layer. Pi Coding Agent, its TUI and its
  unrestricted coding tools are not included.

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
  adapter for its sole Pi backend. No Claudian CLI process management,
  unrestricted file tools, provider implementations, or source files are
  copied into this repository.
