#!/usr/bin/env python3
"""turnburn - onde a cota queima numa sessao do Claude Code.

Le o transcript .jsonl de uma sessao e mostra, turno a turno, para onde foi o
gasto de tokens. O detalhe que importa: token cru engana. Reler o prefixo em
cache custa 0,1x; gerar saida custa 5x. Uma sessao que "parece" 84% cache pode
estar queimando a cota em output. turnburn pesa cada categoria pelo custo
relativo e mostra o gasto efetivo (cota), nao so o volume.

Zero dependencia: so a biblioteca padrao. Rode com o Python que voce ja tem.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass, field

# Multiplicadores relativos ao token de input nao-cacheado (= 1.0).
# Sao as razoes de preco publicadas da Anthropic, nao dolar: a moeda que
# interessa aqui e cota, e cota escala com esses pesos, nao com contagem crua.
DEFAULT_WEIGHTS = {
    "input": 1.0,        # input novo, nao cacheado
    "cache_read": 0.1,   # reler prefixo ja cacheado (barato por token, enorme em volume)
    "cache_5m": 1.25,    # escrever no cache, TTL 5 min
    "cache_1h": 2.0,     # escrever no cache, TTL 1 h
    "output": 5.0,       # tokens gerados (o mais caro por token)
}

CATEGORY_LABELS = {
    "cache_read": "cache relido",
    "cache_5m": "cache 5m",
    "cache_1h": "cache 1h",
    "input": "input novo",
    "output": "output",
}


@dataclass
class Turn:
    index: int
    timestamp: str
    model: str
    cache_read: int = 0
    cache_5m: int = 0
    cache_1h: int = 0
    input: int = 0
    output: int = 0
    thinking: int = 0

    def raw_total(self) -> int:
        return self.cache_read + self.cache_5m + self.cache_1h + self.input + self.output

    def weighted(self, weights: dict) -> float:
        return (
            self.cache_read * weights["cache_read"]
            + self.cache_5m * weights["cache_5m"]
            + self.cache_1h * weights["cache_1h"]
            + self.input * weights["input"]
            + self.output * weights["output"]
        )


@dataclass
class Session:
    path: str
    turns: list = field(default_factory=list)

    def totals(self):
        t = {"cache_read": 0, "cache_5m": 0, "cache_1h": 0, "input": 0, "output": 0}
        for turn in self.turns:
            t["cache_read"] += turn.cache_read
            t["cache_5m"] += turn.cache_5m
            t["cache_1h"] += turn.cache_1h
            t["input"] += turn.input
            t["output"] += turn.output
        return t


def _usage_from_record(rec: dict):
    """Extrai o bloco de usage de uma linha do transcript, ou None."""
    if rec.get("type") != "assistant":
        return None
    msg = rec.get("message") or {}
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    return usage, msg


def parse_session(path: str) -> Session:
    session = Session(path=path)
    idx = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # linha corrompida no meio do log nao derruba a leitura
            got = _usage_from_record(rec)
            if got is None:
                continue
            usage, msg = got
            cache_creation = usage.get("cache_creation") or {}
            details = usage.get("output_tokens_details") or {}
            idx += 1
            session.turns.append(
                Turn(
                    index=idx,
                    timestamp=rec.get("timestamp", ""),
                    model=msg.get("model", ""),
                    cache_read=int(usage.get("cache_read_input_tokens", 0) or 0),
                    cache_5m=int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0),
                    cache_1h=int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0),
                    input=int(usage.get("input_tokens", 0) or 0),
                    output=int(usage.get("output_tokens", 0) or 0),
                    thinking=int(details.get("thinking_tokens", 0) or 0),
                )
            )
    return session


def _fmt(n) -> str:
    if isinstance(n, float):
        n = round(n)
    return f"{int(n):,}".replace(",", ".")


def _pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "0,0%"
    return f"{100.0 * part / whole:.1f}%".replace(".", ",")


def _short_time(ts: str) -> str:
    # 2026-09-30T16:29:00Z -> 16:29
    if "T" in ts and len(ts) >= 16:
        return ts[11:16]
    return ts[:16]


def render_summary(session: Session, weights: dict) -> str:
    out = []
    totals = session.totals()
    raw_all = sum(totals.values())
    weighted_all = sum(t.weighted(weights) for t in session.turns)
    out.append(f"turnburn  {os.path.basename(session.path)}")
    out.append(f"turnos com uso: {len(session.turns)}")
    out.append("")
    if not session.turns:
        out.append("nenhum turno com usage neste transcript.")
        return "\n".join(out)

    out.append("categoria        tokens crus     % cru      cota efetiva    % cota")
    out.append("-" * 70)
    order = ["cache_read", "cache_1h", "cache_5m", "input", "output"]
    for cat in order:
        raw = totals[cat]
        wt = raw * weights[cat]
        out.append(
            f"{CATEGORY_LABELS[cat]:<15} "
            f"{_fmt(raw):>12}   {_pct(raw, raw_all):>6}   "
            f"{_fmt(wt):>12}   {_pct(wt, weighted_all):>6}"
        )
    out.append("-" * 70)
    out.append(
        f"{'total':<15} {_fmt(raw_all):>12}   {'100,0%':>6}   "
        f"{_fmt(weighted_all):>12}   {'100,0%':>6}"
    )
    out.append("")

    # A leitura que o volume cru esconde.
    read_raw = _pct(totals["cache_read"], raw_all)
    read_cost = _pct(totals["cache_read"] * weights["cache_read"], weighted_all)
    out_cost = _pct(totals["output"] * weights["output"], weighted_all)
    out.append(f"cache relido: {read_raw} do volume, mas {read_cost} da cota.")
    out.append(f"output: {out_cost} da cota.")

    top = max(session.turns, key=lambda t: t.weighted(weights))
    out.append(
        f"turno mais caro: #{top.index} ({_short_time(top.timestamp)}) "
        f"= {_pct(top.weighted(weights), weighted_all)} da cota da sessao."
    )
    return "\n".join(out)


def render_by_turn(session: Session, weights: dict, top_n: int) -> str:
    out = []
    weighted_all = sum(t.weighted(weights) for t in session.turns) or 1.0
    turns = sorted(session.turns, key=lambda t: t.weighted(weights), reverse=True)
    if top_n > 0:
        turns = turns[:top_n]
    out.append("turno  hora   cache_relido   cache_novo    input     output   cota    %cota")
    out.append("-" * 78)
    for t in turns:
        cache_new = t.cache_5m + t.cache_1h
        out.append(
            f"#{t.index:<5} {_short_time(t.timestamp):<5} "
            f"{_fmt(t.cache_read):>12}  {_fmt(cache_new):>10}  "
            f"{_fmt(t.input):>8}  {_fmt(t.output):>8}  "
            f"{_fmt(t.weighted(weights)):>8}  {_pct(t.weighted(weights), weighted_all):>6}"
        )
    return "\n".join(out)


def to_json(session: Session, weights: dict) -> str:
    totals = session.totals()
    weighted_all = sum(t.weighted(weights) for t in session.turns)
    payload = {
        "path": session.path,
        "turns": len(session.turns),
        "raw_totals": totals,
        "weighted_total": round(weighted_all, 2),
        "weights": weights,
        "by_turn": [
            {
                "index": t.index,
                "timestamp": t.timestamp,
                "model": t.model,
                "cache_read": t.cache_read,
                "cache_5m": t.cache_5m,
                "cache_1h": t.cache_1h,
                "input": t.input,
                "output": t.output,
                "thinking": t.thinking,
                "weighted": round(t.weighted(weights), 2),
            }
            for t in session.turns
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def find_latest_transcript() -> str | None:
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    hits = glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True)
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def parse_weights_arg(pairs) -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"peso invalido: {pair!r} (use categoria=valor)")
        key, val = pair.split("=", 1)
        key = key.strip()
        if key not in weights:
            raise ValueError(f"categoria desconhecida: {key!r}")
        weights[key] = float(val)
    return weights


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="turnburn",
        description="Onde a cota queima numa sessao do Claude Code.",
    )
    p.add_argument("transcript", nargs="?", help="caminho do .jsonl da sessao")
    p.add_argument("--latest", action="store_true",
                   help="usa o transcript mais recente em ~/.claude/projects")
    p.add_argument("--by-turn", action="store_true",
                   help="tabela por turno, ordenada por cota")
    p.add_argument("--top", type=int, default=0, metavar="N",
                   help="com --by-turn, mostra so os N turnos mais caros")
    p.add_argument("--json", action="store_true", help="saida em JSON")
    p.add_argument("--weight", action="append", metavar="CAT=VAL",
                   help="sobrescreve um peso (ex.: output=8)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path = args.transcript
    if args.latest and not path:
        path = find_latest_transcript()
        if not path:
            print("nenhum transcript encontrado em ~/.claude/projects", file=sys.stderr)
            return 2
    if not path:
        print("informe um transcript .jsonl ou use --latest", file=sys.stderr)
        return 2
    if not os.path.isfile(path):
        print(f"arquivo nao encontrado: {path}", file=sys.stderr)
        return 2
    try:
        weights = parse_weights_arg(args.weight)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    session = parse_session(path)
    if args.json:
        print(to_json(session, weights))
    elif args.by_turn:
        print(render_by_turn(session, weights, args.top))
    else:
        print(render_summary(session, weights))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
