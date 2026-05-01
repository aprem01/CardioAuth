"""Tests for cardioauth.safety_verifier — the independent verification layer.

The safety_verifier re-extracts atomic clinical facts from raw notes
(deterministic regex, no LLM) and cross-checks them against the chart
extraction and the reasoner output. These tests cover:

  - per-fact extractors (positive, negative, denial guards)
  - CPT propagation from note vs chart
  - end-to-end run_safety_verification across realistic scenarios,
    including reproductions of Peter's Case 1 (LBBB + inability to
    exercise + nondiagnostic prior testing all in note, reasoner
    returned 0%) and Case 5 (CPT in note != CPT on chart).
"""

from cardioauth.safety_verifier import (
    FactCheck,
    FactComparison,
    SafetyAuditLog,
    check_attenuation_artifact,
    check_bmi_above_35,
    check_can_exercise_adequately,
    check_inability_to_exercise,
    check_lbbb,
    check_nondiagnostic_prior_testing,
    check_paced_rhythm,
    check_rbbb,
    cpts_mentioned_in_note,
    run_safety_verification,
)


# ──────────────────────────────────────────────────────────────────────
# check_lbbb
# ──────────────────────────────────────────────────────────────────────


def test_check_lbbb_finds_abbreviation():
    fc = check_lbbb("EKG: NSR with LBBB.")
    assert fc.present is True
    assert fc.fact_id == "lbbb_present"
    assert "LBBB" in fc.quote


def test_check_lbbb_finds_full_phrase():
    fc = check_lbbb("ECG shows left bundle branch block, rate 78.")
    assert fc.present is True
    assert "left bundle" in fc.quote.lower()


def test_check_lbbb_case_insensitive():
    fc = check_lbbb("ecg findings: lbbb noted on prior tracing")
    assert fc.present is True


def test_check_lbbb_respects_no_lbbb_denial():
    fc = check_lbbb("EKG: NSR. No LBBB. No RBBB.")
    assert fc.present is False
    assert "denied" in fc.rule_matched


def test_check_lbbb_respects_denies_lbbb():
    fc = check_lbbb("Patient denies LBBB on prior records.")
    assert fc.present is False


def test_check_lbbb_empty_note():
    fc = check_lbbb("")
    assert fc.present is False
    assert fc.quote == ""


def test_check_lbbb_unrelated_text():
    fc = check_lbbb("Patient presents with chest pain and dyspnea on exertion.")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_rbbb
# ──────────────────────────────────────────────────────────────────────


def test_check_rbbb_finds_abbreviation():
    fc = check_rbbb("ECG: RBBB pattern noted.")
    assert fc.present is True
    assert fc.fact_id == "rbbb_present"


def test_check_rbbb_finds_full_phrase():
    fc = check_rbbb("Right bundle branch block on prior EKG.")
    assert fc.present is True


def test_check_rbbb_respects_denial():
    fc = check_rbbb("No RBBB. Normal conduction.")
    assert fc.present is False


def test_check_rbbb_empty_note():
    fc = check_rbbb("")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_paced_rhythm
# ──────────────────────────────────────────────────────────────────────


def test_check_paced_rhythm_finds_paced_rhythm():
    fc = check_paced_rhythm("EKG shows paced rhythm at 70 bpm.")
    assert fc.present is True


def test_check_paced_rhythm_finds_ventricular_paced():
    fc = check_paced_rhythm("Ventricular paced rhythm noted on tracing.")
    assert fc.present is True


def test_check_paced_rhythm_finds_av_paced():
    fc = check_paced_rhythm("AV paced rhythm at 75.")
    assert fc.present is True


def test_check_paced_rhythm_finds_pacemaker_rhythm():
    fc = check_paced_rhythm("Pacemaker rhythm captured throughout the strip.")
    assert fc.present is True


def test_check_paced_rhythm_finds_sp_pacemaker():
    fc = check_paced_rhythm("Pt is s/p pacemaker placement 2019.")
    assert fc.present is True


def test_check_paced_rhythm_negative():
    fc = check_paced_rhythm("Normal sinus rhythm. No pacemaker noted.")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_inability_to_exercise
# ──────────────────────────────────────────────────────────────────────


def test_check_inability_to_exercise_unable_to_exercise():
    fc = check_inability_to_exercise("Patient is unable to exercise on treadmill.")
    assert fc.present is True


def test_check_inability_to_exercise_cannot_perform_treadmill():
    fc = check_inability_to_exercise("Patient cannot perform treadmill due to fatigue.")
    assert fc.present is True


def test_check_inability_to_exercise_deconditioned():
    fc = check_inability_to_exercise("Severely deconditioned, can't tolerate stress.")
    assert fc.present is True


def test_check_inability_to_exercise_wheelchair_bound():
    fc = check_inability_to_exercise("Patient is wheelchair-bound s/p stroke.")
    assert fc.present is True


def test_check_inability_to_exercise_respects_able_denial():
    fc = check_inability_to_exercise("Patient is able to exercise without limitation.")
    assert fc.present is False


def test_check_inability_to_exercise_respects_can_walk_treadmill():
    fc = check_inability_to_exercise("Patient can walk on treadmill at 3 mph.")
    assert fc.present is False


def test_check_inability_to_exercise_negative():
    fc = check_inability_to_exercise("Active patient, jogs daily.")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_can_exercise_adequately
# ──────────────────────────────────────────────────────────────────────


def test_check_can_exercise_adequately_walk_on_treadmill():
    fc = check_can_exercise_adequately("Patient can walk on treadmill at usual pace.")
    assert fc.present is True


def test_check_can_exercise_adequately_achieved_target_hr():
    fc = check_can_exercise_adequately("Achieved target heart rate of 152.")
    assert fc.present is True


def test_check_can_exercise_adequately_achieved_mets():
    fc = check_can_exercise_adequately("Achieved 10 METs on Bruce protocol.")
    assert fc.present is True


def test_check_can_exercise_adequately_completed_treadmill():
    fc = check_can_exercise_adequately("Patient completed treadmill stress without issue.")
    assert fc.present is True


def test_check_can_exercise_adequately_negative():
    fc = check_can_exercise_adequately("Patient cannot perform treadmill.")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_nondiagnostic_prior_testing
# ──────────────────────────────────────────────────────────────────────


def test_check_nondiagnostic_finds_non_diagnostic():
    fc = check_nondiagnostic_prior_testing("Prior ETT was non-diagnostic.")
    assert fc.present is True


def test_check_nondiagnostic_finds_equivocal():
    fc = check_nondiagnostic_prior_testing("Prior SPECT was equivocal.")
    assert fc.present is True


def test_check_nondiagnostic_finds_technically_limited():
    fc = check_nondiagnostic_prior_testing("Prior study technically limited by motion.")
    assert fc.present is True


def test_check_nondiagnostic_finds_inconclusive():
    fc = check_nondiagnostic_prior_testing("Inconclusive stress echo last year.")
    assert fc.present is True


def test_check_nondiagnostic_finds_attenuation_artifact():
    fc = check_nondiagnostic_prior_testing("Findings suspicious for attenuation artifact.")
    assert fc.present is True


def test_check_nondiagnostic_finds_false_positive():
    fc = check_nondiagnostic_prior_testing("Prior result deemed false-positive.")
    assert fc.present is True


def test_check_nondiagnostic_finds_suboptimal():
    fc = check_nondiagnostic_prior_testing("Suboptimal images on prior SPECT.")
    assert fc.present is True


def test_check_nondiagnostic_negative():
    fc = check_nondiagnostic_prior_testing("Prior ETT was diagnostic and normal.")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_attenuation_artifact
# ──────────────────────────────────────────────────────────────────────


def test_check_attenuation_artifact_generic():
    fc = check_attenuation_artifact("Defect likely attenuation artifact.")
    assert fc.present is True


def test_check_attenuation_artifact_breast():
    fc = check_attenuation_artifact("Concerning for breast attenuation on anterior wall.")
    assert fc.present is True


def test_check_attenuation_artifact_diaphragmatic():
    fc = check_attenuation_artifact("Inferior wall defect c/w diaphragmatic attenuation.")
    assert fc.present is True


def test_check_attenuation_artifact_negative():
    fc = check_attenuation_artifact("Normal perfusion images.")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# check_bmi_above_35
# ──────────────────────────────────────────────────────────────────────


def test_check_bmi_above_35_plain_value():
    fc = check_bmi_above_35("BMI 38, hypertensive.")
    assert fc.present is True
    assert "38" in fc.label or "38" in fc.quote


def test_check_bmi_above_35_bmi_of_form():
    fc = check_bmi_above_35("Patient with BMI of 35.")
    assert fc.present is True


def test_check_bmi_above_35_equals_form():
    fc = check_bmi_above_35("Vitals: BMI=42.")
    assert fc.present is True


def test_check_bmi_above_35_below_threshold():
    fc = check_bmi_above_35("BMI 32, well-controlled HTN.")
    assert fc.present is False


def test_check_bmi_above_35_normal_bmi():
    fc = check_bmi_above_35("BMI 25, healthy.")
    assert fc.present is False


def test_check_bmi_above_35_no_bmi_mentioned():
    fc = check_bmi_above_35("Patient has dyspnea on exertion.")
    assert fc.present is False


def test_check_bmi_above_35_empty():
    fc = check_bmi_above_35("")
    assert fc.present is False


# ──────────────────────────────────────────────────────────────────────
# cpts_mentioned_in_note
# ──────────────────────────────────────────────────────────────────────


def test_cpts_mentioned_explicit_tag():
    note = "Ordering CPT 78492 for stress imaging."
    assert "78492" in cpts_mentioned_in_note(note)


def test_cpts_mentioned_bareword_cardiology_range():
    note = "Plan: 78452 (Exercise SPECT)."
    assert "78452" in cpts_mentioned_in_note(note)


def test_cpts_mentioned_skips_non_cardiology():
    # 12345 is not in 33xxx/75xxx/78xxx/92xxx/93xxx ranges.
    note = "Reference number 12345 from prior chart."
    assert cpts_mentioned_in_note(note) == []


def test_cpts_mentioned_multiple_no_duplicates():
    note = "CPT 78492 ordered, but per AUC 78452 may be more appropriate. CPT 78492 again."
    codes = cpts_mentioned_in_note(note)
    assert "78492" in codes
    assert "78452" in codes
    # Each code appears only once.
    assert codes.count("78492") == 1
    assert codes.count("78452") == 1


def test_cpts_mentioned_empty_note():
    assert cpts_mentioned_in_note("") == []


# ──────────────────────────────────────────────────────────────────────
# run_safety_verification — empty note baseline
# ──────────────────────────────────────────────────────────────────────


def test_run_safety_verification_empty_note_no_findings():
    log = run_safety_verification(
        raw_note="",
        chart_data={},
        reasoner_summary=None,
    )
    assert isinstance(log, SafetyAuditLog)
    # Every comparison should mark present=False.
    assert all(c.fact.present is False for c in log.comparisons)
    assert log.note_chart_cpt_mismatch is False
    assert log.cpts_in_note == []


# ──────────────────────────────────────────────────────────────────────
# run_safety_verification — Peter's Case 1 reproduction
#
# Note has LBBB + inability to exercise + non-diagnostic prior ETT.
# Chart has all three (in v2 buckets). Reasoner returned criteria_met=[]
# (the bug we want to catch). Comparator should show three rows where
# present_in_note=True, present_in_chart=True, criterion_met_by_reasoner=False.
# ──────────────────────────────────────────────────────────────────────


def test_run_safety_verification_case_1_three_facts_missed_by_reasoner():
    note = (
        "67 yo F with LBBB on EKG. Recent ETT non-diagnostic due to "
        "attenuation. Cannot perform treadmill due to fatigue."
    )
    chart = {
        "current_symptoms": [{"name": "cannot perform treadmill due to fatigue"}],
        "ecg_findings": [{"conduction": "LBBB"}],
        "prior_stress_tests": [{"interpretation": "non-diagnostic"}],
        "active_comorbidities": [],
        "procedure_code": "78452",
        "additional_notes": "",
    }
    # Reasoner evaluated all three criteria but marked NONE as met (the bug).
    reasoner = {
        "criteria_met": [],
        "criteria_not_met": ["ECG-001", "EX-001", "NDX-001"],
    }

    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary=reasoner,
        resolved_cpt="78452",
    )

    by_id = {c.fact.fact_id: c for c in log.comparisons}

    lbbb = by_id["lbbb_present"]
    assert lbbb.fact.present is True
    assert lbbb.present_in_chart is True
    assert lbbb.criterion_met_by_reasoner is False

    inability = by_id["inability_to_exercise"]
    assert inability.fact.present is True
    assert inability.present_in_chart is True
    assert inability.criterion_met_by_reasoner is False

    ndx = by_id["nondiagnostic_prior_testing"]
    assert ndx.fact.present is True
    assert ndx.present_in_chart is True
    assert ndx.criterion_met_by_reasoner is False

    # All three rows where note + chart both saw the fact, but the reasoner
    # failed to honor any of them as a met criterion.
    rows_reasoner_failed = [
        c for c in log.comparisons
        if c.fact.present and c.present_in_chart and c.criterion_met_by_reasoner is False
    ]
    assert len(rows_reasoner_failed) >= 3


# ──────────────────────────────────────────────────────────────────────
# run_safety_verification — Peter's Case 5 reproduction
#
# Note explicitly says "Ordering CPT 78452 (Exercise SPECT)" but the
# chart's procedure_code is "78492" (the request). Comparator should
# flag note_chart_cpt_mismatch=True.
# ──────────────────────────────────────────────────────────────────────


def test_run_safety_verification_case_5_cpt_mismatch():
    note = (
        "Patient with chest pain. Ordering CPT 78452 (Exercise SPECT) "
        "for further evaluation."
    )
    chart = {
        "current_symptoms": [{"name": "chest pain"}],
        "ecg_findings": [],
        "prior_stress_tests": [],
        "active_comorbidities": [],
        "procedure_code": "78492",
        "additional_notes": "",
    }

    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary={},
        resolved_cpt="78492",
    )

    assert "78452" in log.cpts_in_note
    assert log.cpt_in_chart == "78492"
    assert log.note_chart_cpt_mismatch is True


# ──────────────────────────────────────────────────────────────────────
# run_safety_verification — Clean case (no mismatches)
# ──────────────────────────────────────────────────────────────────────


def test_run_safety_verification_clean_case_all_aligned():
    note = (
        "60 yo M, BMI 38. Prior SPECT was non-diagnostic. "
        "Ordering further imaging."
    )
    chart = {
        "current_symptoms": [{"name": "chest pain"}],
        "ecg_findings": [],
        "prior_stress_tests": [{"interpretation": "non-diagnostic"}],
        "active_comorbidities": ["BMI 38"],
        "procedure_code": "78452",
        "additional_notes": "",
    }
    reasoner = {
        "criteria_met": ["BMI-001", "NDX-001"],
        "criteria_not_met": [],
    }

    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary=reasoner,
        resolved_cpt="78452",
    )

    by_id = {c.fact.fact_id: c for c in log.comparisons}

    bmi = by_id["bmi_above_35"]
    assert bmi.fact.present is True
    assert bmi.present_in_chart is True
    assert bmi.criterion_met_by_reasoner is True

    ndx = by_id["nondiagnostic_prior_testing"]
    assert ndx.fact.present is True
    assert ndx.present_in_chart is True
    assert ndx.criterion_met_by_reasoner is True

    assert log.note_chart_cpt_mismatch is False


# ──────────────────────────────────────────────────────────────────────
# run_safety_verification — Chart extraction missed it
# ──────────────────────────────────────────────────────────────────────


def test_run_safety_verification_chart_missed_fact_in_note():
    note = "EKG: NSR with LBBB, rate 72."
    chart = {
        "current_symptoms": [],
        "ecg_findings": [],          # chart extractor missed LBBB
        "prior_stress_tests": [],
        "active_comorbidities": [],
        "procedure_code": "78452",
        "additional_notes": "",
    }

    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary={},
    )
    by_id = {c.fact.fact_id: c for c in log.comparisons}

    lbbb = by_id["lbbb_present"]
    assert lbbb.fact.present is True       # safety verifier found it
    assert lbbb.present_in_chart is False  # chart did not


# ──────────────────────────────────────────────────────────────────────
# run_safety_verification — Reasoner did not evaluate the criterion
# ──────────────────────────────────────────────────────────────────────


def test_run_safety_verification_reasoner_did_not_evaluate_criterion():
    note = "EKG with LBBB pattern."
    chart = {
        "current_symptoms": [],
        "ecg_findings": [{"conduction": "LBBB"}],
        "prior_stress_tests": [],
        "active_comorbidities": [],
        "procedure_code": "78452",
        "additional_notes": "",
    }
    # Reasoner evaluated nothing related to ECG-001.
    reasoner = {
        "criteria_met": ["BMI-001"],
        "criteria_not_met": ["EX-001"],
    }

    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary=reasoner,
    )
    by_id = {c.fact.fact_id: c for c in log.comparisons}

    lbbb = by_id["lbbb_present"]
    assert lbbb.fact.present is True
    assert lbbb.present_in_chart is True
    # ECG-001 wasn't in the evaluated set → criterion_met_by_reasoner is None.
    assert lbbb.criterion_met_by_reasoner is None


# ──────────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────────


def test_chart_data_with_nested_list_of_dicts_uses_name_field():
    """Comparator should inspect dict values inside list-of-dicts buckets."""
    note = "Patient cannot perform treadmill due to severe arthritis."
    chart = {
        "current_symptoms": [{"name": "cannot perform treadmill"}],
        "ecg_findings": [],
        "prior_stress_tests": [],
        "active_comorbidities": [],
        "procedure_code": "78452",
        "additional_notes": "",
    }
    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary={},
    )
    by_id = {c.fact.fact_id: c for c in log.comparisons}
    inability = by_id["inability_to_exercise"]
    assert inability.fact.present is True
    assert inability.present_in_chart is True


def test_denial_guard_handles_able_to_exercise():
    """Note saying patient is able to exercise → inability_to_exercise=False."""
    fc = check_inability_to_exercise(
        "Patient is able to exercise without limitation."
    )
    assert fc.present is False


def test_factcheck_quote_non_empty_when_present():
    fc = check_lbbb("Recent EKG: LBBB. Otherwise normal.")
    assert fc.present is True
    assert fc.quote != ""
    assert fc.char_start < fc.char_end


def test_factcheck_to_dict_round_trip_serializable():
    fc = check_lbbb("LBBB on EKG.")
    d = fc.to_dict()
    assert d["fact_id"] == "lbbb_present"
    assert d["present"] is True
    # No nested dataclass leaks — every value is a primitive.
    for v in d.values():
        assert isinstance(v, (str, int, bool, float))


def test_factcomparison_to_dict_serializable():
    fc = check_lbbb("LBBB on EKG.")
    comp = FactComparison(
        fact=fc,
        present_in_chart=True,
        chart_evidence="ecg_findings",
        relevant_criteria=["ECG-001"],
        criterion_met_by_reasoner=False,
    )
    d = comp.to_dict()
    # `fact` field is itself serialized as a dict (not a dataclass).
    assert isinstance(d["fact"], dict)
    assert d["present_in_chart"] is True
    assert d["chart_evidence"] == "ecg_findings"
    assert d["relevant_criteria"] == ["ECG-001"]
    assert d["criterion_met_by_reasoner"] is False


def test_safetyauditlog_to_dict_serializable():
    log = run_safety_verification(
        raw_note="LBBB on EKG. Ordering CPT 78452.",
        chart_data={"procedure_code": "78492"},
        reasoner_summary={},
    )
    d = log.to_dict()
    assert isinstance(d["comparisons"], list)
    assert all(isinstance(c, dict) for c in d["comparisons"])
    assert isinstance(d["cpts_in_note"], list)
    assert d["cpt_in_chart"] == "78492"
    assert d["note_chart_cpt_mismatch"] is True


def test_run_safety_verification_reasoner_summary_with_dict_codes():
    """reasoner_summary can carry dicts with 'code' instead of plain strings."""
    note = "EKG with LBBB."
    chart = {
        "ecg_findings": [{"conduction": "LBBB"}],
        "procedure_code": "78452",
    }
    reasoner = {
        "criteria_met": [{"code": "ECG-001", "label": "Conduction abnormality"}],
        "criteria_not_met": [],
    }
    log = run_safety_verification(
        raw_note=note,
        chart_data=chart,
        reasoner_summary=reasoner,
    )
    by_id = {c.fact.fact_id: c for c in log.comparisons}
    lbbb = by_id["lbbb_present"]
    assert lbbb.criterion_met_by_reasoner is True
