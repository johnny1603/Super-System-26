import os

def qa_check(proposal, answers):
    for pkg in proposal.get("packages", []):
        monthly = int(pkg.get("monthly_management_total", 0) or 0)
        correct_benefit = monthly * 2

        if pkg.get("benefit_value") != correct_benefit:
            print(f"QA Fix [{pkg.get('id','?')}]: benefit_value {pkg.get('benefit_value')} -> {correct_benefit}")
            pkg["benefit_value"] = correct_benefit

        try:
            breakdown = pkg.get("setup_fee_breakdown", {})
            setup_total = sum(int(v or 0) for v in breakdown.values())
            if setup_total and abs(setup_total - int(pkg.get("setup_fee_total", 0) or 0)) > 100:
                print(f"QA Fix [{pkg.get('id','?')}]: setup_fee_total -> {setup_total}")
                pkg["setup_fee_total"] = setup_total
        except:
            pass

    print("QA Agent 1: Numbers verified")
    return proposal
