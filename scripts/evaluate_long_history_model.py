"""Run inside the isolated synthetic backend after copying both evaluation scripts.

Never mutates provider configuration. Exercises the same answer service with an
explicit per-run model and records the actual model IDs returned by OpenAI.
"""

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

from check_long_history import CASES, EVENTS, PREFIX, normalize


async def main() -> None:
    if os.environ.get('AUTH_SECRET') != 'semantic-public-fixture-00000000000000000000' or os.environ.get('CONNECTOR_NETWORK_ENABLED') != 'false':
        raise RuntimeError('Model comparison requires the isolated synthetic backend')
    from sqlalchemy import select

    from app.database.session import session_factory
    from app.integrations.providers import (
        OpenAIProvider,
        OpenAITransport,
        ProviderError,
    )
    from app.models.entities import Conversation, Message
    from app.schemas.domain import Query
    from app.services.knowledge import answer_query
    from app.services.settings import runtime_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, choices=['gpt-4.1-mini', 'gpt-4.1', 'gpt-5.4-mini'])
    parser.add_argument('--cases', nargs='*')
    parser.add_argument('--repeat', type=int, choices=[1, 2], default=1)
    parser.add_argument('--output', default='/tmp/model-evaluation.json')
    args = parser.parse_args()
    actual_models: set[str] = set()

    class AuditedTransport(OpenAITransport):
        async def post(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            if args.model == 'gpt-5.4-mini' and endpoint == 'chat/completions':
                payload = {**payload, 'reasoning_effort': 'none'}
            result = await super().post(endpoint, payload)
            model = result.get('model')
            if not isinstance(model, str) or not (model == args.model or model.startswith(args.model + '-')):
                raise ProviderError('Model comparison returned an unexpected model identity')
            actual_models.add(model)
            return result

    results = []
    with session_factory()() as session:
        settings = runtime_settings(session).model_copy(update={'llm_model': args.model})
        from app.integrations.providers import providers
        llm, embeddings = providers(settings)
        assert isinstance(llm, OpenAIProvider)
        llm.transport = AuditedTransport(settings)
        known = {}
        conversations = {}
        for topic, events in EVENTS.items():
            conversation = session.scalar(select(Conversation).where(Conversation.title == PREFIX + topic))
            assert conversation
            conversations[topic] = conversation.id
            for event in events:
                message = session.scalar(select(Message).where(Message.conversation_id == conversation.id, Message.content == event.text))
                assert message
                known[event.key] = message.id
        for repetition in range(args.repeat):
            for case in CASES:
                if args.cases and case.key not in args.cases:
                    continue
                started = time.monotonic()
                row = {'case': case.key, 'model': args.model, 'repetition': repetition + 1}
                try:
                    query = Query.model_validate({'question': case.question, 'date_to': case.date_to, 'previous_question': 'Bia rompeu a parceria com Nilo?' if case.key == 'topic_switch' else None})
                    answer = await answer_query(session, settings, llm, embeddings, query)
                    text = ' '.join(claim.text for claim in answer.claims)
                    normalized = normalize(text)
                    cited = {source.id for source in answer.sources}
                    inspected = cited | {source.id for source in answer.context_sources}
                    checks = {
                        'answers_when_supported': bool(answer.claims) if case.evidence else answer.insufficient_evidence and not answer.claims,
                        'concepts_present': all(any(re.search(variant, normalized) for variant in alternatives) for alternatives in case.concepts),
                        'decisive_evidence_inspected': all(known[key] in inspected for key in case.evidence),
                        'decisive_evidence_cited': all(known[key] in cited for key in case.evidence),
                        'correct_conversation': all(source.conversation_id == conversations[case.conversation] for source in answer.sources),
                        'no_forbidden_claims': not any(re.search(value, normalized) for value in case.forbidden) and 'ABACAXI-ADMIN' not in text,
                    }
                    row.update(passed=all(checks.values()), checks=checks, answer=text, steps=[step.model_dump() for step in answer.research_steps], limited=answer.research_limited, missing_inspected=[key for key in case.evidence if known[key] not in inspected], missing_cited=[key for key in case.evidence if known[key] not in cited])
                    session.rollback()  # Release source-validation read locks between questions.
                except ProviderError as exc:
                    session.rollback()
                    row.update(passed=False, error=str(exc))
                row['elapsed_seconds'] = round(time.monotonic() - started, 2)
                results.append(row)
                report = {'model': args.model, 'dataset_revision': 2, 'actual_models': sorted(actual_models), 'messages': 3341, 'days': 92, 'passed': sum(item['passed'] for item in results), 'total': len(results), 'results': results}
                Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(row, ensure_ascii=True), flush=True)
    print(json.dumps({key: value for key, value in report.items() if key != 'results'}), flush=True)
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
