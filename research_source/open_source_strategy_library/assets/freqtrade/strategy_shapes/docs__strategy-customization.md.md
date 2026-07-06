# Freqtrade - docs/strategy-customization.md

- Source ID: `freqtrade`
- Origin: `https://github.com/freqtrade/freqtrade`
- Origin ref: `4de16ac9521079ad03ab97f296bea49ceadb9f6f`
- Remote path: `docs/strategy-customization.md`
- License: `GPL-3.0`
- License policy: `distilled_research_only`
- Asset type: `strategy_shape`
- Extraction tags: strategy_template, technical_signal

## Distilled Summary

# Strategy Customization This page explains how to customize your strategies, add new indicators and set up trading rules. If you haven't already, please familiarize yourself with: - the [Freqtrade strategy 101](strategy-101.md), which provides a quick start to strategy development - the [Freqtrade bot basics](bot-basics.md), which provides overall info on how the bot operates ## Develop your own strategy The bot includes a default strategy file. Also, several other strategies are available in the [strategy repository](https://github.com/freqtrade/freqtrade-strategies). You will however most likely have your own idea for a strategy. This document intends to help you convert your ideas into a working strategy. ### Generating a strategy template To get started, you can use the command: ```ba

## RAG Notes

- # Strategy Customization
- - the [Freqtrade strategy 101](strategy-101.md), which provides a quick start to strategy development
- ## Develop your own strategy
- The bot includes a default strategy file.
- Also, several other strategies are available in the [strategy repository](https://github.com/freqtrade/freqtrade-strategies).
- You will however most likely have your own idea for a strategy.
- This document intends to help you convert your ideas into a working strategy.
- ### Generating a strategy template
- freqtrade new-strategy --strategy AwesomeStrategy
- This will create a new strategy called `AwesomeStrategy` from a template, which will be located using the filename `user_data/strategies/AwesomeStrategy.py`.
- There is a difference between the *name* of the strategy and the filename. In most commands, Freqtrade uses the *name* of the strategy, *not the filename*.
- The `new-strategy` command generates starting examples which will not be profitable out of the box.
- `freqtrade new-strategy` has an additional parameter, `--template`, which controls the amount of pre-build information you get in the created strategy. Use `--template minimal` to get an empty strategy without any indicator examples, or `--template advanced` t
- ### Anatomy of a strategy
- A strategy file contains all the information needed to build the strategy logic:
- - Candle data in OHLCV format

## Safety Boundary

This is a distilled research asset. It is not imported as runtime trading code; any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.
