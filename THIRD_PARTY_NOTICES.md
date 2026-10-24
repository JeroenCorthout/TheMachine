# Third-party notices

The Machine is open source under AGPL-3.0-only (see [LICENSE](LICENSE)). It does not copy or bundle third-party source code (one font aside, below): it runs on top of software you install yourself, which keeps its own licence. This file lists that software and its licence, for convenience.

## OpenClaw

The Machine is an OpenClaw plugin and service. The installer installs OpenClaw from npm (`npm install -g openclaw`); that package ships its own `LICENSE` and `THIRD_PARTY_NOTICES.md`. The plugin imports OpenClaw's plugin SDK at run time from your installation.

```
MIT License

Copyright (c) 2026 OpenClaw Foundation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Node packages

The installer installs these from npm at the exact versions in `package-lock.json` (`npm ci`, no install scripts). The card chat uses them to read a card's session from the OpenClaw Gateway. Each package ships its own licence.

| Package | Version | Licence |
| --- | --- | --- |
| `@openclaw/gateway-client` | 2026.8.1 | MIT, OpenClaw Foundation |
| `@openclaw/gateway-protocol` | 2026.8.1 | MIT, OpenClaw Foundation |
| `ws` | 8.21.3 | MIT |
| `ipaddr.js` | 2.5.0 | MIT |
| `typebox` | 1.3.16 | MIT |

## Optional tools

Installed from their own package sources only when you use the feature that needs them. Each keeps its own licence and copyright notice.

| Software | Used for | Licence |
| --- | --- | --- |
| Node.js | runs OpenClaw and the plugin | MIT |
| Python | runs the service | PSF License |
| Tailscale | private dashboard access | BSD-3-Clause |
| [`wacli`](https://github.com/openclaw/wacli), installed from the [Salesflare/wacli](https://github.com/Salesflare/wacli) fork (upstream plus read-only profile picture lookups) | local WhatsApp store, owner-approved WhatsApp sends and profile pictures | MIT, Copyright (c) 2026 Peter Steinberger |

## Source Serif 4

The dashboard's headings use Source Serif 4 by Adobe, bundled unmodified as `brand/fonts/source-serif-4-latin.woff2` (the Latin subset Google Fonts serves). It is licensed under the SIL Open Font License 1.1; the full licence is in [brand/fonts/OFL.txt](brand/fonts/OFL.txt).

## Inter

The dashboard's interface text uses Inter by The Inter Project Authors, bundled unmodified as `brand/fonts/inter-latin.woff2` (the Latin subset Google Fonts serves). It is licensed under the SIL Open Font License 1.1; the full licence is in [brand/fonts/Inter-OFL.txt](brand/fonts/Inter-OFL.txt).
