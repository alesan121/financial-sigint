"""
src/orchestrator/cli.py - Maintenance and Diagnostics Terminal.
DEFCON 1 Version: Manual Signal Injection and Rich Telemetry.

Hardware analogy: This is the serial terminal (UART) that we connect
directly to the motherboard to inject test voltages and
read the CPU registers without going through the external network bus.
"""

import asyncio
import logging
import re
import sys
import uuid
from datetime import UTC, datetime

from orchestrator.graph import run_trading_cycle

# 🛰️ PATCH: Rich for high-fidelity visual output
_RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    # Calibration for modern terminals (avoids encoding noise on Windows)
    _console: Console | None = Console(force_terminal=True, legacy_windows=False)
    _RICH_AVAILABLE = True
except ImportError:
    _console = None

# Silence external library noise to keep the log bus clean
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("orchestrator")
logger.setLevel(logging.INFO)

DEFAULT_NEWS = (
    "NVIDIA ($NVDA) reports record-breaking data center revenue, "
    "surpassing analyst expectations by 20%. The stock is up 5% in after-hours trading."
)


def _strip_markup(text: str) -> str:
    """Signal cleanup filter."""
    return re.sub(r"\[/?[^\]]*\]", "", text)


def _print_state(state: dict, thread_id: str) -> None:
    """
    State Oscilloscope: Renders the FSM's final registers.
    Maps the ingress_signal and risk_quant sub-buses after systemic filtering.
    Aligned with the ISA defined in state.py.
    """
    ingress = state.get("ingress_signal", {})
    risk = state.get("risk_quant", {})
    exec_p = state.get("execution_payload", {})

    # 🔧 SRE V4.0: Extraction of new backplane registers (Channel 2: RiskQuant)
    port_status = state.get("portfolio_risk_status", "N/A")
    market_regime = risk.get("market_regime", "UNKNOWN")
    vix_level = risk.get("vix_level", 0.0)
    kalman_p = risk.get("kalman_price", 0.0)
    retry_count = state.get("retry_count", 0)

    # Output relay state resolution (Channel 3: ExecutionPayload)
    action = exec_p.get("action", risk.get("routing_flag", "PENDING"))

    # Color logic for the front panel
    color = (
        "green"
        if action in ("APPROVED", "LONG", "EXECUTED")
        else "red" if action == "REJECTED" else "yellow"
    )
    port_color = "green" if port_status == "PASS" else "red" if port_status != "N/A" else "white"

    if _RICH_AVAILABLE and _console is not None:
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("Register (Bus)", style="bold yellow", width=22)
        table.add_column("Current Value", style="white")

        # --- CHANNEL 0: SYSTEM ---
        table.add_row("[dim]SYS:[/dim] Bus ID", f"[blue]{thread_id}[/blue]")
        table.add_row("[dim]SYS:[/dim] Retries", str(retry_count))

        # --- CHANNEL 1: INGRESS ---
        table.add_row(
            "[dim]ING:[/dim] Ticker ID", f"[bold white]{ingress.get('ticker', 'N/A')}[/bold white]"
        )
        table.add_row("[dim]ING:[/dim] Sentiment", str(ingress.get("sentiment", "N/A")))
        table.add_row("[dim]ING:[/dim] SNR (Conf)", f"{ingress.get('stoch_confidence', 0.0):.2f}")

        # --- CHANNEL 2: RISK/DSP ---
        table.add_row("[dim]RSK:[/dim] Regime", f"[magenta]{market_regime}[/magenta]")
        table.add_row("[dim]RSK:[/dim] VIX Level", f"{vix_level:.2f}")
        table.add_row("[dim]RSK:[/dim] Kalman P", f"${kalman_p:.2f}")
        table.add_row("[dim]RSK:[/dim] PoP Score", f"{risk.get('pop_score', 0.0):.4f}")
        table.add_row("[dim]RSK:[/dim] Allocation", f"${risk.get('allocation_usd', 0.0):,.2f}")

        # --- CHANNEL 3: EXECUTION ---
        table.add_row(
            "[bold]PORT:[/bold] Guard", f"[bold {port_color}]{port_status}[/bold {port_color}]"
        )
        table.add_row("[bold]FINAL:[/bold] ACTION", f"[bold {color}]{action}[/bold {color}]")

        _console.print()
        _console.print(
            Panel(table, title="[bold]FSM REGISTER READOUT v4.0[/bold]", border_style="blue")
        )

        # Node Audit (Audit Trail / Plant Logs)
        _console.print("\n[bold]Audit Trail (Component Sequence):[/bold]")
        for i, entry in enumerate(state.get("logs", []), 1):
            _console.print(f"  [dim]{i:02}[/dim] {entry}")
        _console.print()
    else:
        # Fallback for terminals without ANSI support (Plain Text Mode)
        print("\n" + "=" * 60)
        print(f"  FSM RESULT - BUS: {thread_id} | REGIME: {market_regime}")
        print("=" * 60)
        print(f"  Ticker    : {ingress.get('ticker')}")
        print(f"  Sentiment : {ingress.get('sentiment')}")
        print(f"  VIX/Kalman: {vix_level:.2f} / ${kalman_p:.2f}")
        print(f"  Action    : {action}")
        print("=" * 60)


async def main(news_text: str) -> None:
    """CLI Ignition Sequence."""
    ts = datetime.now(UTC).isoformat()
    # Generate a unique identifier for this test 'pulse'
    thread_id = f"CLI-{uuid.uuid4().hex[:6].upper()}"

    if _RICH_AVAILABLE and _console:
        _console.print(f"\n[bold blue]{'='*70}[/bold blue]")
        _console.print("🚀 [bold]FINANCIAL SIGINT[/bold] | DEFCON 1 Diagnostics Terminal")
        _console.print(f"[bold blue]{'='*70}[/bold blue]\n")
        _console.print(f"[yellow]Trigger Time :[/yellow] {ts}")
        _console.print(f"[yellow]Signal ID    :[/yellow] {thread_id}")

    preview = news_text[:120] + "..." if len(news_text) > 120 else news_text
    if _console:
        _console.print(f'\n[bold]Injecting Signal:[/bold] [italic]"{preview}"[/italic]\n')
        _console.print("[bold cyan]>> Processing in the LangGraph Backplane...[/bold cyan]")

    try:
        # Signal injection into the main bus (FSM)
        final_state = await run_trading_cycle(news_text, thread_id=thread_id)
        _print_state(final_state, thread_id)
    except Exception as e:
        if _console:
            _console.print(f"\n[bold red]💥 KERNEL PANIC: {e}[/bold red]")
        else:
            print(f"Error: {e}")
        sys.exit(1)


def _parse_args() -> str:
    import argparse

    parser = argparse.ArgumentParser(description="SIGINT CLI Terminal")
    parser.add_argument("--news", type=str, help="News text to inject")
    args = parser.parse_args()
    return args.news if args.news else DEFAULT_NEWS


if __name__ == "__main__":
    try:
        asyncio.run(main(_parse_args()))
    except KeyboardInterrupt:
        print("\nAborting sequence...")
