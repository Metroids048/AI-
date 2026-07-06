# Jesse - README.md

- Source ID: `jesse`
- Origin: `https://github.com/jesse-ai/jesse`
- Origin ref: `7296cec497870215cb343047e81dac36ab3f2223`
- Remote path: `README.md`
- License: `MIT`
- License policy: `distilled_research_allowed`
- Asset type: `documentation`
- Extraction tags: backtesting, paper_trading, strategy_template

## Distilled Summary

<div align="center"> <br> <p align="center"> <img src="assets/jesse-logo.png" alt="Jesse" height="72" /> </p> <p align="center"> Algo-trading was 😵‍💫, we made it 🤩 </p> </div> # Jesse [![PyPI](https://img.shields.io/pypi/v/jesse)](https://pypi.org/project/jesse) [![Downloads](https://pepy.tech/badge/jesse)](https://pepy.tech/project/jesse) [![Docker Pulls](https://img.shields.io/docker/pulls/salehmir/jesse)](https://hub.docker.com/r/salehmir/jesse) [![GitHub](https://img.shields.io/github/license/jesse-ai/jesse)](https://github.com/jesse-ai/jesse) [![coverage](https://codecov.io/gh/jesse-ai/jesse/graph/badge.svg)](https://codecov.io/gh/jesse-ai/jesse) --- Jesse is an advanced crypto trading framework that aims to **simplify** **researching** and defining **YOUR OWN trading strategies** for

## RAG Notes

- Jesse is an advanced crypto trading framework that aims to **simplify** **researching** and defining **YOUR OWN trading strategies** for backtesting, optimizing, and live trading.
- - ⏰ **Multiple Timeframes and Symbols**: Backtest and livetrade multiple timeframes and symbols simultaneously without look-ahead bias.
- - 🔒 **Self-Hosted and Privacy-First**: Designed with your privacy in mind, fully self-hosted to ensure your trading strategies and data remain secure.
- - 🛡️ **Risk Management**: Built-in helper functions for robust risk management.
- - 📋 **Metrics System**: A comprehensive metrics system to evaluate your trading strategy's performance.
- - 🔍 **Debug Mode**: Observe your strategy in action with a detailed debug mode.
- - 🧠 **Machine Learning**: A built-in ML pipeline — gather labelled training data from backtests, train scikit-learn models (binary, multiclass, or regression), and deploy predictions directly inside your strategies.
- Craft complex trading strategies with remarkably simple Python. Access 300+ indicators, multi-symbol/timeframe support, spot/futures trading, partial fills, and risk management tools. Focus on logic, not boilerplate.
- class GoldenCross(Strategy):
- ### Backtest
- Execute highly accurate and fast backtests without look-ahead bias. Utilize debugging logs, interactive charts with indicator support, and detailed performance metrics to validate your strategies thoroughly.
- ![Backtest](https://raw.githubusercontent.com/jesse-ai/storage/refs/heads/master/backtest.gif)
- ### Live/Paper Trading
- Deploy strategies live with robust monitoring tools. Supports paper trading, multiple accounts, real-time logs & notifications (Telegram, Slack, Discord), interactive charts, spot/futures, DEX, and a built-in code editor.
- ![Live/Paper Trading](https://raw.githubusercontent.com/jesse-ai/storage/refs/heads/master/live.gif)
- Accelerate research using the benchmark feature. Run batch backtests, compare across timeframes, symbols, and strategies. Filter and sort results by key performance metrics for efficient analysis.

## Safety Boundary

This is a distilled research asset. It is not imported as runtime trading code; any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.
