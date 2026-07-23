# Stage Count Model

| stage | expected input | accepted | rejected | explicit error | observed status |
|---|---:|---:|---:|---:|---|
| cycle | 103 scheduler slots | 102 completed | 1 claimed | unknown | PARTIAL |
| decision snapshot | 195 decisions/24h | unknown | unknown | 17 gateway failures after intent | UNOBSERVABLE |
| ensemble | observation 137 lane decisions | unknown | 82 discard | unknown | PARTIAL |
| LLM | decisions reaching LLM | unknown | 0 veto observed | unknown | PARTIAL |
| gateway | 17 directional candidates | 0 acknowledged | 17 failed | 17 ValueError | OBSERVED FAILURE |
| position reconcile | exchange positions | unknown | unknown | unknown | UNOBSERVABLE |
| exit | local close records 2 + exchange order 1 | exchange order confirmed | local records `exchange_already_flat` | timeline mismatch | PARTIAL |

任何“accepted + rejected = input”的数字若无法由事件行直接计算，均标为 unknown。
