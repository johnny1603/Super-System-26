import anthropic
import json
import os

def filter_questions(base_questions: list, answers: dict, api_key: str) -> list:
    """
    בודק אילו שאלות כבר נענו בפתיחה החופשית ומסנן אותן
    """
    client = anthropic.Anthropic(api_key=api_key)
    
    intro = answers.get("intro", "")
    if not intro:
        return base_questions
    
    questions_list = [{"id": q["id"], "text": q["text"]} for q in base_questions]
    
    system = """You are a smart question filter for an onboarding chatbot.
The client already wrote an opening message describing their business.
Your job is to identify which follow-up questions are already answered in that opening message.

Rules:
- If the client mentioned how long the business exists -> skip 'business_age'
- If the client mentioned financial situation (profit/loss) -> skip 'financial_status'  
- If the client mentioned their marketing budget -> skip 'marketing_budget'
- If the client mentioned their digital presence -> skip 'existing_digital'
- If the client clearly stated their main goal -> skip 'main_goal'
- Only skip if you are VERY confident the answer is clear
- When in doubt, keep the question

Return JSON only:
{"skip_ids": ["id1", "id2"]}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": f"Client opening message: {intro}\n\nQuestions to check: {json.dumps(questions_list)}"}]
    )
    
    raw = response.content[0].text.replace("```json","").replace("```","").strip()
    skip_ids = json.loads(raw).get("skip_ids", [])
    
    if skip_ids:
        print(f"Question filter: skipping {skip_ids}")
    
    filtered = [q for q in base_questions if q["id"] not in skip_ids]
    return filtered
