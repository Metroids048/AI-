# Freqtrade - docs/hyperopt.md

- Source ID: `freqtrade`
- Origin: `https://github.com/freqtrade/freqtrade`
- Origin ref: `4de16ac9521079ad03ab97f296bea49ceadb9f6f`
- Remote path: `docs/hyperopt.md`
- License: `GPL-3.0`
- License policy: `distilled_research_only`
- Asset type: `workflow_note`
- Extraction tags: optimization, parameter_search

## Distilled Summary

# Hyperopt This page explains how to tune your strategy by finding the optimal parameters, a process called hyperparameter optimization. The bot uses algorithms included in the `optuna` package to accomplish this. The search will burn all your CPU cores, make your laptop sound like a fighter jet and still take a long time. In general, the search for best parameters starts with a few random combinations (see [below](#reproducible-results) for more details) and then uses one of optuna's sampler algorithms (currently NSGAIIISampler) to quickly find a combination of parameters in the search hyperspace that minimizes the value of the [loss function](#loss-functions). Hyperopt requires historic data to be available, just as backtesting does (hyperopt runs backtesting many times with different pa

## RAG Notes

- This page explains how to tune your strategy by finding the optimal
- parameters, a process called hyperparameter optimization. The bot uses algorithms included in the `optuna` package to accomplish this.
- Hyperopt requires historic data to be available, just as backtesting does (hyperopt runs backtesting many times with different parameters).
- To learn how to get data for the pairs and exchange you're interested in, head over to the [Data Downloading](data-download.md) section of the documentation.
- Hyperopt can crash when used with only 1 CPU Core as found out in [Issue #1133](https://github.com/freqtrade/freqtrade/issues/1133)
- Since 2021.4 release you no longer have to write a separate hyperopt class, but can configure the parameters directly in the strategy.
- ## Install hyperopt dependencies
- Since Hyperopt dependencies are not needed to run the bot itself, are heavy, can not be easily built on some platforms (like Raspberry PI), they are not installed by default. Before you run Hyperopt, you need to install the corresponding dependencies, as descr
- Since Hyperopt is a resource intensive process, running it on a Raspberry Pi is not recommended nor supported.
- The docker-image includes hyperopt dependencies, no further action needed.
- pip install -r requirements-hyperopt.txt
- ## Hyperopt command reference
- --8<-- "commands/hyperopt.md"
- ### Hyperopt checklist
- Checklist on all tasks / possibilities in hyperopt
- * define parameters with `space='buy'` - for entry signal optimization

## Safety Boundary

This is a distilled research asset. It is not imported as runtime trading code; any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.
