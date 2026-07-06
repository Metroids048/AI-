# NautilusTrader - README.md

- Source ID: `nautilus_trader`
- Origin: `https://github.com/nautechsystems/nautilus_trader`
- Origin ref: `692410c522770c878d4a51ca24c487df2d0933cf`
- Remote path: `README.md`
- License: `LGPL-3.0`
- License policy: `distilled_research_only`
- Asset type: `architecture_note`
- Extraction tags: research_live_parity, event_driven, backtesting

## Distilled Summary

# <img src="https://github.com/nautechsystems/nautilus_trader/raw/develop/assets/nautilus-trader-logo.png" width="500"> [![codecov](https://codecov.io/gh/nautechsystems/nautilus_trader/branch/master/graph/badge.svg?token=DXO9QQI40H)](https://codecov.io/gh/nautechsystems/nautilus_trader) [![codspeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://codspeed.io/nautechsystems/nautilus_trader) ![pythons](https://img.shields.io/pypi/pyversions/nautilus_trader) ![pypi-version](https://img.shields.io/pypi/v/nautilus_trader) ![pypi-format](https://img.shields.io/pypi/format/nautilus_trader?color=blue) [![Downloads](https://img.shields.io/pepy/dt/nautilus-trader?color=blue)](https://pepy.tech/projects/nautilus-trader) [![Discord](https://img.shields.io/badge/Discord-%23

## RAG Notes

- The system spans research, deterministic simulation, and live execution within a single
- event-driven architecture, with Python serving as the control plane for strategy logic,
- the flexibility of Python for system composition and strategy development.
- The same execution semantics and deterministic time model operate in both research and
- live systems. Strategies deploy from research to production with no code changes,
- providing research-to-live parity and reducing the divergence that typically introduces
- deployment risk.
- - **Backtesting**: Multiple venues, instruments, and strategies simultaneously using historical quote tick, trade tick, bar, order book, and custom data with nanosecond resolution.
- - **Live**: Identical strategy implementations between research and live deployment.
- - **AI Training**: Engine fast enough to train AI trading agents (RL/ES).
- Trading strategy research is often conducted in Python using vectorized approaches, while
- A Rust-native core provides a deterministic event-driven runtime for both research and live
- from research to production without reimplementation.
- and data providers by translating their raw APIs into a unified interface and normalized domain model.
- | [Databento](https://databento.com) | `DATABENTO` | Data Provider | ![status](https://img.shields.io/badge/stable-green) | [Guide](docs/integrations/databento.md) |
- | [Tardis](https://tardis.dev) | `TARDIS` | Crypto Data Provider | ![status](https://img.shields.io/badge/stable-green) | [Guide](docs/integrations/tardis.md) |

## Safety Boundary

This is a distilled research asset. It is not imported as runtime trading code; any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.
