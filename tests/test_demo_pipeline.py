"""Cross-repository integration checks for the web adapter and QA pipeline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from backend import dataset, qa as qa_adapter
from soprano_qa import llm as llm_module
from soprano_qa.llm import GenerationBackendUnavailable
from soprano_qa.settings import load_settings

qa = qa_adapter.SERVICE

MOOD_QUERY = "28마디부터 분위기 변화를 어떻게 표현해야 하나요?"
MOOD_UNIT_ID = "die-forelle-ku-010"
SECOND_BEAT_ACCENT_UNIT_ID = "die-forelle-ku-002"
OUT_OF_DOMAIN_QUERY = "2022년 FIFA 월드컵 우승팀은 어디인가요?"
MOOD_LYRIC = "end- -lich ward dem"
GROUNDED_ANSWER = "피아노가 만드는 긴장감을 이용해 전환이 느껴지도록 부릅니다."


class DemoPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        # The service eagerly loads the 14B GGUF onto the (shared) GPU before
        # any generation request. Every generation test replaces the model
        # call itself, so the preflight is replaced too; tests that exercise
        # the preflight failure override it locally.
        patcher = mock.patch.object(qa, "validate_generation_requirements")
        self.generation_preflight = patcher.start()
        self.addCleanup(patcher.stop)

    def test_adapter_loads_pipeline_from_sibling_repository(self) -> None:
        self.assertEqual(
            qa_adapter.PIPELINE_ROOT,
            Path(__file__).resolve().parents[2] / "soprano-qa-rag-system",
        )
        self.assertTrue(Path(qa.__file__).is_relative_to(qa_adapter.PIPELINE_ROOT))

    def test_adapter_loads_media_from_sibling_dataset_repository(self) -> None:
        expected_dataset = Path(__file__).resolve().parents[2] / "soprano-qa-dataset"
        self.assertEqual(dataset.DATASET_ROOT, expected_dataset)
        self.assertEqual(
            dataset.SCORE_ASSET_ROOT,
            expected_dataset / "sheet_music",
        )

    def test_pipeline_loads_active_review_and_web_data_from_dataset_main_checkout(
        self,
    ) -> None:
        expected_dataset = (
            Path(__file__).resolve().parents[2] / "soprano-qa-dataset"
        ).resolve()
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = load_settings(use_legacy_dataset_env=False)

        self.assertEqual(Path(settings["dataset_root"]), expected_dataset)
        self.assertTrue(
            (expected_dataset / "database" / "exports" / "research-open.jsonl").is_file()
        )
        review_root = expected_dataset / "expert_curation" / "review"
        self.assertEqual(
            {path.stem for path in review_root.glob("*.json")},
            {
                "die-forelle",
                "in-flowery-clouds",
                "la-capinera",
                "nella-fantasia",
                "una-voce-poco-fa",
            },
        )

    def test_demo_does_not_vendor_pipeline_or_dataset_artifacts(self) -> None:
        demo_root = Path(__file__).resolve().parents[1]
        for pipeline_or_data_directory in (
            "soprano_qa",
            "config",
            "data",
            "assets",
        ):
            self.assertFalse(
                (demo_root / pipeline_or_data_directory).exists(),
                pipeline_or_data_directory,
            )

    def test_demo_dataset_and_rag_dataset_overrides_do_not_collide(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "SOPRANO_QA_DATASET_ROOT": "/tmp/score-dataset",
                "SOPRANO_QA_RAG_DATASET_ROOT": "/tmp/rag-dataset",
                "SOPRANO_QA_MODEL_PATH": "/tmp/model.gguf",
            },
            clear=False,
        ):
            settings = load_settings(use_legacy_dataset_env=False)

        self.assertEqual(settings["dataset_root"], "/tmp/rag-dataset")
        self.assertEqual(settings["model_path"], "/tmp/model.gguf")

    def test_measure_query_uses_pipeline_scope_and_ids(self) -> None:
        result = qa.ask(
            piece_id="die-forelle",
            question=MOOD_QUERY,
            measure_range=(28, 30),
            generate=False,
        )

        self.assertEqual(result["pipeline"], "soprano_qa")
        self.assertEqual(result["generation_mode"], "retrieval_only")
        self.assertEqual(result["answer_basis"], "retrieved_evidence")
        self.assertEqual(result["evidence"][0]["id"], MOOD_UNIT_ID)
        self.assertEqual(
            result["evidence"][0]["text"],
            (
                "피아노 파트에서 바뀌는 음형은 낚시꾼이 물을 흐트러뜨리며 "
                "물고기를 잡는 모습을 그려, 곡의 내용과 밀접하게 연결된 "
                "긴장감과 분위기 전환을 만든다. 노래하는 사람은 이를 이해하고 "
                "피아노가 만드는 긴장감을 이용해 가사를 표현하며, 전환이 "
                "느껴지도록 주의해서 부르는 것이 좋다."
            ),
        )
        self.assertEqual(
            result["evidence"][0]["scope_match"],
            "overlaps_query_range",
        )
        self.assertEqual(
            result["evidence"][0]["generation_role"],
            "selected_range_support",
        )
        self.assertTrue(result["evidence"][0]["selected_range_claim_authority"])
        self.assertTrue(result["has_selected_range_grounding"])
        # The extractive answer carries the evidence text; record IDs stay in
        # the structured evidence rather than the displayed prose.
        self.assertIn("긴장감과 분위기 전환", result["answer"])
        self.assertNotIn(MOOD_UNIT_ID, result["answer"])
        self.generation_preflight.assert_not_called()

    def test_selected_range_returns_score_context_lyrics(self) -> None:
        result = qa.ask(
            piece_id="die-forelle",
            question=MOOD_QUERY,
            measure_range=(28, 30),
            generate=False,
        )

        context = result["score_context"]
        self.assertEqual(context["status"], "model_transcribed_unverified")
        self.assertEqual(
            [measure["measure"] for measure in context["measures"]],
            [28],
        )
        self.assertIn(MOOD_LYRIC, context["measures"][0]["lyric_lines"])

    def test_korean_query_retrieves_reviewed_second_beat_accent_unit(self) -> None:
        result = qa.ask(
            piece_id="die-forelle",
            question="피아노 반주에서 두 번째 박의 악센트는 무엇을 나타내는가?",
            measure_range=(2, 5),
            generate=False,
        )

        self.assertEqual(result["evidence"][0]["id"], SECOND_BEAT_ACCENT_UNIT_ID)
        self.assertIn("송어가 뛰어노는 모습", result["answer"])

    def test_generation_runs_context_and_citation_pipeline(self) -> None:
        with mock.patch.object(
            qa,
            "generate_llm",
            return_value=f"{GROUNDED_ANSWER} [E1]",
        ) as generate:
            result = qa.ask(
                piece_id="die-forelle",
                question=MOOD_QUERY,
                measure_range=(28, 30),
                generate=True,
            )

        self.generation_preflight.assert_called_once_with(
            qa.SETTINGS["model_path"],
            qa.SETTINGS["llm"],
        )
        generate.assert_called_once()
        prompt = generate.call_args.args[1][1]["content"]
        # The question and evidence travel as JSON data, and the same bundle
        # is handed to the backend's admission and output guards.
        payload = json.loads(prompt)
        self.assertEqual(generate.call_args.kwargs["security_context"], payload)
        first_evidence = payload["Retrieved evidence"][0]
        self.assertEqual(first_evidence["citation_label"], "E1")
        self.assertIn("긴장감과 분위기 전환", first_evidence["answer"])
        self.assertIn(MOOD_LYRIC, prompt)
        self.assertEqual(result["generation_mode"], "llm")
        self.assertEqual(result["answer_basis"], "retrieved_evidence")
        self.assertIsNone(result["generation_validation_warning"])
        self.assertEqual(result["evidence"][0]["id"], MOOD_UNIT_ID)
        # Temporary [E#] labels are validated and removed; record IDs are
        # never printed in the answer.
        self.assertEqual(result["answer"], GROUNDED_ANSWER)
        self.assertNotIn(MOOD_UNIT_ID, result["answer"])

    def test_no_hit_retrieval_reports_no_corpus_evidence(self) -> None:
        result = qa.ask(
            piece_id="die-forelle",
            question=OUT_OF_DOMAIN_QUERY,
            measure_range=None,
            generate=False,
        )

        self.assertEqual(result["generation_mode"], "unavailable")
        self.assertEqual(result["answer_basis"], "no_corpus_evidence")
        self.assertEqual(result["unavailable_reason"], "no_corpus_evidence")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["evidence_notices"], [])

    @unittest.expectedFailure
    def test_no_hit_generation_does_not_answer_from_internal_knowledge(
        self,
    ) -> None:
        """An out-of-domain question must not reach the model as grounded.

        Known upstream failure: ``retrieve_generation_evidence`` in
        soprano-qa-rag-system supplements an empty ``index.search`` result
        with low-scoring whole-piece expert units and web_coaching claims, so
        this question is answered by the LLM with ``answer_basis`` =
        ``retrieved_evidence``. Remove the decorator once that is fixed.
        """
        with mock.patch.object(
            qa,
            "generate_llm",
            return_value="일반 음악 지식으로 답한 내용입니다. [E1]",
        ) as generate:
            result = qa.ask(
                piece_id="die-forelle",
                question=OUT_OF_DOMAIN_QUERY,
                measure_range=None,
                generate=True,
            )

        generate.assert_not_called()
        self.assertEqual(result["generation_mode"], "unavailable")
        self.assertEqual(result["answer_basis"], "no_corpus_evidence")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(
            result["answer"],
            qa.NO_CORPUS_EVIDENCE_MESSAGE,
        )

    def assertDraftWithheld(self, result: dict) -> None:
        # A rejected draft comes back as the fixed refusal with no evidence.
        self.assertEqual(result["generation_mode"], "unavailable")
        self.assertEqual(result["unavailable_reason"], "answer_validation_failed")
        self.assertEqual(result["answer"], qa.NO_CORPUS_EVIDENCE_MESSAGE)
        self.assertEqual(result["evidence"], [])

    def test_grounded_refusal_is_rejected_not_displayed(self) -> None:
        refusal = "제공된 근거만으로는 이 질문에 정확히 답변할 수 없습니다."
        with mock.patch.object(
            qa,
            "generate_llm",
            return_value=refusal,
        ) as generate:
            result = qa.ask(
                piece_id="die-forelle",
                question=MOOD_QUERY,
                measure_range=(28, 30),
                generate=True,
            )

        generate.assert_called_once()
        self.assertIn(
            "Retrieved evidence",
            generate.call_args.args[1][1]["content"],
        )
        self.assertDraftWithheld(result)
        self.assertNotIn(refusal, result["answer"])

    def test_empty_grounded_draft_is_rejected(self) -> None:
        with mock.patch.object(
            qa,
            "generate_llm",
            return_value=" \n\t",
        ) as generate:
            result = qa.ask(
                piece_id="die-forelle",
                question=MOOD_QUERY,
                measure_range=(28, 30),
                generate=True,
            )

        generate.assert_called_once()
        self.assertDraftWithheld(result)

    def test_grounded_secondary_limitation_does_not_trigger_internal_retry(self) -> None:
        with mock.patch.object(
            qa,
            "generate_llm",
            return_value=(
                "제공된 근거로는 판본 정보가 부족하지만, 28마디의 "
                "분위기 변화는 어둡게 표현합니다. [E1]"
            ),
        ) as generate:
            result = qa.ask(
                piece_id="die-forelle",
                question=MOOD_QUERY,
                measure_range=(28, 30),
                generate=True,
            )

        generate.assert_called_once()
        self.assertEqual(result["generation_mode"], "llm")
        self.assertEqual(result["answer_basis"], "retrieved_evidence")
        self.assertTrue(result["evidence"])
        self.assertIn("분위기 변화는 어둡게 표현합니다.", result["answer"])
        self.assertNotIn("[E1]", result["answer"])
        self.assertNotIn(MOOD_UNIT_ID, result["answer"])

    def test_unavailable_generation_backend_fails_before_retrieval(self) -> None:
        failure = GenerationBackendUnavailable("checkpoint missing")
        with (
            mock.patch.object(
                qa,
                "validate_generation_requirements",
                side_effect=failure,
            ),
            mock.patch.object(qa, "_get_index") as get_index,
            mock.patch.object(qa, "generate_llm") as generate,
        ):
            for measure_range in ((28, 30), None):
                with self.subTest(measure_range=measure_range):
                    with self.assertRaises(GenerationBackendUnavailable) as raised:
                        qa.ask(
                            piece_id="die-forelle",
                            question=MOOD_QUERY if measure_range else OUT_OF_DOMAIN_QUERY,
                            measure_range=measure_range,
                            generate=True,
                        )
                    self.assertIs(raised.exception, failure)

        get_index.assert_not_called()
        generate.assert_not_called()

    def test_generation_failure_propagates_after_one_call(self) -> None:
        failure = RuntimeError("test generation failure")
        with mock.patch.object(
            qa,
            "generate_llm",
            side_effect=failure,
        ) as generate:
            with self.assertRaises(RuntimeError) as raised:
                qa.ask(
                    piece_id="die-forelle",
                    question=MOOD_QUERY,
                    measure_range=(28, 30),
                    generate=True,
                )

        self.assertIs(raised.exception, failure)
        generate.assert_called_once()

    def test_exhausted_context_propagates_without_retry(self) -> None:
        with mock.patch.object(
            qa,
            "generate_llm",
            side_effect=ValueError("requested tokens exceed the context window"),
        ) as generate:
            with self.assertRaisesRegex(ValueError, "context window"):
                qa.ask(
                    piece_id="die-forelle",
                    question=MOOD_QUERY,
                    measure_range=(28, 30),
                    generate=True,
                    top_k=2,
                )

        generate.assert_called_once()

    def test_concurrent_generation_is_serialized(self) -> None:
        # Exercise the pipeline's real generate(), with its admission and
        # output guards, and its inference lock; only the llama.cpp model
        # object is replaced. Each completion holds the lock on its own.
        counter_lock = threading.Lock()
        active = 0
        peak = 0
        completions = {"answer": 0, "guard": 0}

        class FakeLlama:
            def create_chat_completion(self, **kwargs) -> dict:
                nonlocal active, peak
                with counter_lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                # Only the answer composition is constrained by a JSON schema;
                # the guards return a bare verdict.
                kind = "answer" if "response_format" in kwargs else "guard"
                with counter_lock:
                    active -= 1
                    completions[kind] += 1
                content = (
                    json.dumps(
                        {"sentences": [{"text": GROUNDED_ANSWER, "evidence": ["E1"]}]},
                        ensure_ascii=False,
                    )
                    if kind == "answer"
                    else '{"allow": true}'
                )
                return {"choices": [{"message": {"content": content}}]}

        def ask_once() -> dict:
            return qa.ask(
                piece_id="die-forelle",
                question=MOOD_QUERY,
                measure_range=(28, 30),
                generate=True,
            )

        with (
            mock.patch.object(llm_module, "load_llama", return_value=FakeLlama()),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _: ask_once(), range(2)))

        self.assertEqual(peak, 1)
        self.assertEqual(completions, {"answer": 2, "guard": 4})
        self.assertTrue(
            all(result["generation_mode"] == "llm" for result in results)
        )

    def test_web_results_keep_deterministic_rights_notices(self) -> None:
        result = qa.ask(
            piece_id="die-forelle",
            question="이 작품의 정식 표제와 작품 번호는 무엇인가요?",
            measure_range=None,
            generate=False,
        )

        self.assertTrue(result["evidence"])
        self.assertTrue(all(item["kind"] == "web" for item in result["evidence"]))
        self.assertEqual(
            {item["record_id"] for item in result["evidence_notices"]},
            {item["id"] for item in result["evidence"]},
        )

    def test_corpus_stats_come_from_pipeline_snapshot(self) -> None:
        stats = qa.corpus_stats()
        self.assertEqual(stats["pipeline"], "soprano_qa")
        self.assertEqual(stats["corpus_schema_version"], 9)
        self.assertEqual(stats["total_records"], 953)
        self.assertEqual(
            stats["records_by_evidence_type"],
            {
                "expert_annotation": 122,
                "sumijo_feedback": 7,
                "web_coaching": 723,
                "web_database": 101,
            },
        )
        self.assertEqual(
            sum(stats["web_coaching_records_by_topic"].values()),
            stats["records_by_evidence_type"]["web_coaching"],
        )
        self.assertEqual(
            stats["expert_records_by_measure_status"],
            {"specific": 65, "unspecified": 3, "whole_piece": 54},
        )
        self.assertEqual(stats["model"]["backend"], "llama-cpp-python")


if __name__ == "__main__":
    unittest.main()
