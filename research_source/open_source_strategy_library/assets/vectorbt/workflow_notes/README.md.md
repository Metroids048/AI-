# vectorbt - README.md

- Source ID: `vectorbt`
- Origin: `https://github.com/polakowo/vectorbt`
- Origin ref: `e0e8460dd90aaa0034ee3cffb94bb8de2511358f`
- Remote path: `README.md`
- License: `Apache-2.0`
- License policy: `distilled_research_allowed`
- Asset type: `architecture_note`
- Extraction tags: vectorized_backtesting, parameter_grid

## Distilled Summary

<div align="center"> <a href="https://vectorbt.pro/" title="VectorBT PRO"> <img src="https://raw.githubusercontent.com/polakowo/vectorbt/master/docs/docs/assets/logo/header-pro.svg" /> </a> </div> <div align="center"> <a href="https://vectorbt.dev/" title="vectorbt"> <img src="https://raw.githubusercontent.com/polakowo/vectorbt/master/docs/docs/assets/logo/header.svg" /> </a> </div> <br> <p align="center"> <a href="https://pepy.tech/project/vectorbt" title="Downloads"> <img src="https://img.shields.io/pepy/dt/vectorbt?label=downloads&color=blue" /> </a> <a href="https://pypi.org/project/vectorbt" title="PyPI"> <img src="https://img.shields.io/pypi/v/vectorbt" /> </a> <a href="https://pypi.org/project/vectorbt" title="Supported Python versions"> <img src="https://img.shields.io/pypi/pyversi

## RAG Notes

- <h3 align="center"><b>Thinks in matrices, backtests at scale.</b></h3>
- <p align="center">VectorBT takes a radically different approach to backtesting: instead of looping through bars one strategy at a time, it packs thousands of configurations into NumPy arrays, accelerates the hot path with Numba and Rust, and runs them all at o
- Explore thousands of trading ideas across assets and timeframes, analyze portfolio performance down to individual trades, and visualize results interactively, all in a few lines of code. Built for both human researchers and AI agents, VectorBT combines large-s
- VectorBT is the open-source community edition of [VectorBT PRO](https://vectorbt.pro/), a state-of-the-art hybrid backtesting library.
- - **Fast, vectorized backtesting** and strategy research built on pandas, NumPy, and Numba
- - **Portfolio backtesting** with trade, drawdown, and performance analytics, including QuantStats integration
- - **Signal tooling** for generation, ranking, mapping, and distribution analysis
- - **Built-in data access** with preprocessing and synthetic data generation
- - **Robustness testing** with walk-forward optimization and label generation for ML workflows
- - **Composable Python API** for rapid experimentation and AI agent-driven workflows
- data = vbt.YFData.download("BTC-USD")
- price = data.get("Close")
- pf = vbt.Portfolio.from_holding(price, init_cash=100)
- ### Trade a dual-SMA crossover strategy
- pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=100)
- data = vbt.YFData.download(symbols, missing_index="drop")

## Safety Boundary

This is a distilled research asset. It is not imported as runtime trading code; any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.
