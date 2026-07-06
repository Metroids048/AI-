# OpenBB - README.md

- Source ID: `openbb`
- Origin: `https://github.com/OpenBB-finance/OpenBB`
- Origin ref: `52a446cba01dfead6cd275ec63ef397eaf823c1e`
- Remote path: `README.md`
- License: `AGPL-3.0`
- License policy: `distilled_research_only`
- Asset type: `architecture_note`
- Extraction tags: data_layer, multi_asset_data, ai_agent_data

## Distilled Summary

<br /> <img src="https://github.com/OpenBB-finance/OpenBB/blob/develop/images/odp-light.svg?raw=true#gh-light-mode-only" alt="Open Data Platform by OpenBB logo" width="600"> <img src="https://github.com/OpenBB-finance/OpenBB/blob/develop/images/odp-dark.svg?raw=true#gh-dark-mode-only" alt="Open Data Platform by OpenBB logo" width="600"> <br /> <br /> [![Twitter](https://img.shields.io/twitter/url/https/twitter.com/openbb_finance.svg?style=social&label=Follow%20%40openbb_finance)](https://x.com/openbb_finance) [![Discord Shield](https://img.shields.io/discord/831165782750789672)](https://discord.com/invite/xPHTuHCmuV) [![Open in Dev Containers](https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-

## RAG Notes

- <img src="https://github.com/OpenBB-finance/OpenBB/blob/develop/images/odp-light.svg?raw=true#gh-light-mode-only" alt="Open Data Platform by OpenBB logo" width="600">
- <img src="https://github.com/OpenBB-finance/OpenBB/blob/develop/images/odp-dark.svg?raw=true#gh-dark-mode-only" alt="Open Data Platform by OpenBB logo" width="600">
- <a target="_blank" href="https://colab.research.google.com/github/OpenBB-finance/OpenBB/blob/develop/examples/googleColab.ipynb">
- <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/>
- Open Data Platform by OpenBB (ODP) is the open-source toolset that helps data engineers integrate proprietary, licensed, and public data sources into downstream applications like AI copilots and research dashboards.
- ODP operates as the "connect once, consume everywhere" infrastructure layer that consolidates and exposes data to multiple surfaces at once: Python environments for quants, OpenBB Workspace and Excel for analysts, MCP servers for AI agents, and REST APIs for o
- df = output.to_dataframe()
- Data integrations available can be found here: <https://docs.openbb.co/python/reference>
- While the Open Data Platform provides the open-source data integration foundation, **OpenBB Workspace** offers the enterprise UI for analysts to visualize datasets and leverage AI agents. The platform's "connect once, consume everywhere" architecture enables s
- Data integration:
- - You can learn more about adding data to the OpenBB workspace from the [docs](https://docs.openbb.co/workspace) or [this open source repository](https://github.com/OpenBB-finance/backends-for-openbb).
- AI Agents integration:
- - You can learn more about adding AI agents to the OpenBB workspace from [this open source repository](https://github.com/OpenBB-finance/agents-for-openbb).
- ### Integrating Open Data Platform to the OpenBB Workspace
- Name: Open Data Platform
- Trading in financial instruments involves high risks including the risk of losing some, or all, of your investment

## Safety Boundary

This is a distilled research asset. It is not imported as runtime trading code; any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.
