"""CardioAuth CLI entry point — for development and testing."""

from __future__ import annotations

import json
import logging
import sys

from cardioauth.config import Config
from cardioauth.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("cardioauth")


def main() -> None:
    config = Config()
    missing = config.validate()
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Set them in your .env or environment before running.")
        sys.exit(1)

    orchestrator = Orchestrator(config)

    # Example request — replace with real trigger from Epic webhook / CLI args
    patient_id = sys.argv[1] if len(sys.argv) > 1 else "EXAMPLE-PATIENT-001"
    procedure_code = sys.argv[2] if len(sys.argv) > 2 else "93458"  # Left heart cath
    payer_id = sys.argv[3] if len(sys.argv) > 3 else "PAYER-001"
    payer_name = sys.argv[4] if len(sys.argv) > 4 else "UnitedHealthcare"

    # Steps 1-3: Build review package
    review = orchestrator.process_request(patient_id, procedure_code, payer_id, payer_name)

    print("\n" + "=" * 60)
    print("REVIEW PACKAGE FOR CARDIOLOGIST")
    print("=" * 60)
    print(f"\nApproval Likelihood: {review.reasoning.approval_likelihood_label} "
          f"({review.reasoning.approval_likelihood_score:.0%})")

    if review.requires_human_action:
        print("\n⚠ ACTION REQUIRED:")
        for flag in review.requires_human_action:
            print(f"  - {flag}")

    print(f"\nPA Narrative Draft ({len(review.reasoning.pa_narrative_draft.split())} words):")
    print("-" * 40)
    print(review.reasoning.pa_narrative_draft)
    print("-" * 40)

    if review.reasoning.criteria_not_met:
        print("\nCriteria NOT Met:")
        for gap in review.reasoning.criteria_not_met:
            print(f"  - {gap.criterion}: {gap.gap}")
            print(f"    Recommendation: {gap.recommendation}")

    # Step 4: Wait for human approval
    print("\n" + "=" * 60)
    approval = input("Approve submission? (yes/no): ").strip().lower()
    if approval != "yes":
        print("Submission cancelled by physician.")
        sys.exit(0)

    approver = input("Approving physician name: ").strip()
    if not approver:
        print("Approver name required.")
        sys.exit(1)

    submission = orchestrator.submit_after_approval(review, approved_by=approver)
    print(f"\nSubmitted: {submission.submission_id}")
    print(f"Channel: {submission.submission_channel}")
    print(f"Expected decision by: {submission.expected_decision_date}")
    print(f"Follow-up scheduled: {submission.follow_up_scheduled}")
    print(json.dumps(submission.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    main()
