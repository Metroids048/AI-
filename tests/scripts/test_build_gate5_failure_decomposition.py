from scripts.build_gate5_failure_decomposition import rank_root_causes


def test_root_cause_ranking_does_not_call_unknown_cost_a_loss_cause() -> None:
    ranking = rank_root_causes(
        [
            {"taxonomy": {"primary": "DIRECTION_FAILURE"}, "cost_truth": {"net_pnl_status": "UNKNOWN_FUNDING"}},
            {"taxonomy": {"primary": "DIRECTION_FAILURE"}, "cost_truth": {"net_pnl_status": "UNKNOWN_FUNDING"}},
            {"taxonomy": {"primary": "STOP_GEOMETRY_FAILURE"}, "cost_truth": {"net_pnl_status": "UNKNOWN_FUNDING"}},
            {"taxonomy": {"primary": "GOOD_WIN"}, "cost_truth": {"net_pnl_status": "UNKNOWN_FUNDING"}},
        ]
    )

    assert ranking[0]["root_cause"] == "DIRECTION_FAILURE"
    assert ranking[0]["episodes"] == 2
    assert all(item["root_cause"] != "COST_FAILURE" for item in ranking)
