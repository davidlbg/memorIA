"""Bounded real-provider evaluation on public synthetic fixtures only."""
import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
import httpx

URL = "http://127.0.0.1:58004"
TOKEN = "semantic-public-fixture-00000000000000000000"
TITLE = "SINTETICO MemorIA — contexto de Helena"
FACTS = [
    (0, "Helena", "Sou arquiteta e trabalho com restauro de edifícios históricos na cooperativa Pedra Clara."),
    (3, "Helena", "Moro em São João del-Rei. Minha irmã é Teresa; ela cuida do ateliê quando viajo."),
    (7, "Helena", "Não como castanhas, tenho alergia. No café prefiro chá sem açúcar."),
    (11, "Helena", "Estou estudando italiano para pesquisar os arquivos de Florença; a viagem ainda não está marcada."),
    (17, "Helena", "No projeto Casarão, fiquei responsável pelo levantamento das fachadas. O cálculo estrutural é do Bruno."),
    (24, "Helena", "O orçamento do Casarão era R$ 36 mil, mas o total aprovado agora é R$ 42 mil. Equipamentos já estão incluídos."),
    (32, "Helena", "Prefiro revisar os desenhos juntos às terças, por vídeo. Não consigo nas manhãs de quinta por causa das aulas."),
    (41, "Helena", "Casarão: a proposta de entrega era 18 de setembro, ainda dependia do acesso ao prédio."),
    (49, "Helena", "Casarão: acesso liberado. Confirmamos a entrega para 2 de outubro; a proposta de setembro foi superada."),
    (53, "Helena", "Me mudei para Tiradentes. Teresa continua em São João del-Rei e segue cuidando do ateliê."),
    (61, "Helena", "Casarão: o levantamento das fachadas já foi entregue e aceito. O cálculo estrutural do Bruno segue pendente."),
    (68, "Helena", "Casarão: entrada de R$ 12 mil recebida. Restam R$ 30 mil, só após a aprovação do cálculo estrutural; ainda não foram pagos."),
    (74, "Helena", "Para a visita ao Museu das Trilhas, combinamos sábado 24 de outubro às 14h. Eu levo os ingressos; você encontra a Teresa na recepção."),
    (79, "Helena", "Atualização: a viagem a Florença continua sem data. O resto do planejamento segue como conversamos."),
]
CONCEPTS = [r"arquitet", r"restaur", r"pedra clara", r"teresa", r"tiradentes", r"alerg|castanha", r"cha|açucar", r"italian|florenc", r"fachad", r"bruno", r"42", r"terca|terça", r"2 de outubro|02/10", r"30", r"24 de outubro|24/10"]
CASES = [
    ("overview", "Quem é Helena? Quero uma visão informativa sobre ela e sobre nosso contexto, do começo até agora.", CONCEPTS, []),
    ("specific_payment", "Qual valor do Casarão ainda falta pagar e sob qual condição? Responda só isso.", [r"30", r"aprov", r"estrutural"], [r"italian|florenc|alerg|museu|restauro"]),
    ("specific_current_city", "Onde Helena mora atualmente?", [r"tiradentes"], [r"italian|florenc|alerg|museu|orçamento"]),
    ("specific_meeting", "Quando e onde combinamos a visita ao museu, quem leva os ingressos e quem encontro lá?", [r"24", r"14", r"museu das trilhas", r"helena", r"teresa", r"recep"], [r"italian|florenc|alerg|42 mil"]),
    ("unknown", "Qual é o sobrenome completo da Helena?", [], []),
]

def normalize(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))

def main():
    p=argparse.ArgumentParser();p.add_argument("--output",required=True);p.add_argument("--baseline",action="store_true");a=p.parse_args()
    with httpx.Client(base_url=URL, headers={"Authorization":"Bearer "+TOKEN},timeout=180,trust_env=False) as c:
        health=c.get("/api/v1/health");health.raise_for_status();assert health.json()["external_ai_enabled"]
        chats=c.get("/api/v1/conversations").json();matches=[chat for chat in chats if chat["title"]==TITLE]
        if not matches:
            records=[];start=datetime(2026,8,1)
            for day in range(80):
                for minute in range(8):
                    date=start+timedelta(days=day,hours=8,minutes=minute)
                    records.append((date,"Helena" if minute%2 else "Eu",f"Rotina do dia {day}: confirmei o recebimento da mensagem {minute}, sem novidades."))
            for day,author,text in FACTS:records.append((start+timedelta(days=day,hours=9),author,text))
            content="\n".join(f"{dt:%d/%m/%Y, %H:%M} - {author}: {text}" for dt,author,text in sorted(records)).encode()
            files={"file":("memoria-informative.txt",content,"text/plain")};data={"date_order":"DMY","timezone":"America/Sao_Paulo"}
            preview=c.post("/api/v1/imports/preview",files=files,data=data);preview.raise_for_status()
            imported=c.post("/api/v1/imports",files=files,data={**data,"title":TITLE,"preview_hash":preview.json()["file_hash"]});imported.raise_for_status();cid=imported.json()["conversation_id"]
        else:assert len(matches)==1;cid=matches[0]["id"]
        rows=[]
        for key,question,concepts,forbidden in CASES[:2] if a.baseline else CASES:
            started=time.monotonic();r=c.post("/api/v1/assistant/query",json={"question":question,"conversation_id":cid,"compact_context":True,"timezone":"America/Sao_Paulo"});elapsed=round(time.monotonic()-started,2)
            row={"case":key,"status":r.status_code,"seconds":elapsed}
            if r.status_code==200:
                ans=r.json();text=" ".join(claim["text"] for claim in ans["claims"]);norm=normalize(text)
                checks={"useful_facts":all(re.search(normalize(pattern),norm) for pattern in concepts),"no_unrelated_topics":not any(re.search(normalize(pattern),norm) for pattern in forbidden),"answers_supported":bool(ans["claims"]) if key!="unknown" else ans["insufficient_evidence"] and not ans["claims"],"citations_present":bool(ans["sources"]) if key!="unknown" else True}
                if not a.baseline:
                    checks["correct_mode"]=ans.get("response_mode")==("overview" if key=="overview" else "focused")
                    if key=="overview":checks["complete_reading"]=bool(ans.get("coverage")) and all(item["complete"] for item in ans["coverage"])
                row.update(answer=text,checks=checks,passed=all(checks.values()),coverage=ans.get("coverage"),steps=ans.get("research_steps"),payload_bytes=len(r.content))
            else:row.update(passed=False,error=r.json())
            rows.append(row);Path(a.output).write_text(json.dumps({"passed":sum(item["passed"] for item in rows),"total":len(rows),"results":rows},ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({k:v for k,v in row.items() if k not in {"answer","error"}},ensure_ascii=True),flush=True)
        if not all(row["passed"] for row in rows):raise SystemExit(1)
if __name__=="__main__":main()
