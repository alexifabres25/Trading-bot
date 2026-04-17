"""
Point d'entrée du backtest.

Usage :
    # Backtest simple sur 3 ans avec les paramètres actuels
    python backtest/run.py

    # Backtest sur une paire spécifique
    python backtest/run.py --symbol ETH/USDT --days 365

    # Optimisation des paramètres (plus long)
    python backtest/run.py --optimize

    # Optimisation sur 2 ans
    python backtest/run.py --optimize --days 730
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Permet d'importer les modules du projet depuis n'importe où
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

import config
from backtest.engine import run_backtest
from backtest.optimizer import optimize


def main():
    parser = argparse.ArgumentParser(description="Backtest du trading bot")
    parser.add_argument("--symbol", default=None, help="Paire ex: BTC/USDT")
    parser.add_argument("--days", type=int, default=1095, help="Jours d'historique (défaut: 1095 = 3 ans)")
    parser.add_argument("--optimize", action="store_true", help="Lance l'optimisation des paramètres")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else config.TRADING_PAIRS

    if args.optimize:
        print(f"\n{'='*60}")
        print(f"OPTIMISATION DES PARAMÈTRES — {args.days} jours")
        print(f"{'='*60}\n")
        all_best = {}
        for symbol in symbols:
            print(f"\n▶ Optimisation {symbol}...")
            best = optimize(symbol, days=min(args.days, 730))
            all_best[symbol] = best
            if best:
                print(f"\n✅ Meilleurs paramètres pour {symbol} :")
                print(json.dumps(best, indent=2))

        output = Path("backtest_optimal_params.json")
        output.write_text(json.dumps(all_best, indent=2))
        print(f"\n💾 Paramètres sauvegardés dans {output}")

    else:
        print(f"\n{'='*60}")
        print(f"BACKTEST — {args.days} jours ({args.days//365} an(s))")
        print(f"{'='*60}\n")

        for symbol in symbols:
            print(f"\n▶ Backtest {symbol}...")
            result = run_backtest(symbol, days=args.days)
            print(f"\n{'─'*40}")
            print(f"  {symbol} — {args.days} jours")
            print(f"{'─'*40}")
            print(result.summary())

            if result.nb_trades > 0:
                wins   = [t for t in result.trades if t.pnl_net_pct > 0]
                losses = [t for t in result.trades if t.pnl_net_pct <= 0]
                print(f"\n  Détail des sorties :")
                reasons = {}
                for t in result.trades:
                    reasons[t.reason] = reasons.get(t.reason, 0) + 1
                for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
                    print(f"    {r:<15} : {n} trades")

        print(f"\n{'='*60}")
        print("Pour optimiser les paramètres : python backtest/run.py --optimize")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
