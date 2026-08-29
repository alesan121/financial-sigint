"""
shared/math.py - Core Mathematics & Position Sizing.

Hardware analogy: Arithmetic Logic Unit (ALU). Pure functions
with no infrastructure or state dependencies (stateless).
Allows isolated unit testing and avoids 'spaghetti code' in the Agents.
"""

def compute_kelly_fraction(p: float, entry: float, tp: float, sl: float, max_kelly: float = 0.25) -> tuple[float, str]:
    """
    Computes the optimal Kelly fraction under the binary options model.

    Analogy: This is the voltage divider of the capital circuit.
    Calculates how much energy (money) the load can absorb without blowing the fuse.

    Args:
        p: Estimated probability of success (0.0 to 1.0)
        entry: Entry price (Current voltage)
        tp: Target Take Profit (Saturation limit)
        sl: Target Stop Loss (Cut-off limit)
        max_kelly: Safety cap to limit exposure (default 25%)

    Returns:
        tuple[float, str]: (Recommended capital fraction, textual audit path)
    """
    risk = entry - sl
    reward = tp - entry

    # 🛡️ SHORT-CIRCUIT PROTECTION: Invalid technical levels
    if risk <= 0 or reward <= 0:
        return 0.0, f"p={p:.3f}, b=undefined (entry={entry}, tp={tp}, sl={sl}). f*=0.0"

    # b = Reward/Risk Ratio (Amplifier gain)
    b = reward / risk

    # Master Kelly Formula: f* = (p*b - q) / b  where q = 1-p
    f_star = (p * b - (1 - p)) / b

    # Apply Clamping (Amplitude limitation)
    f_capped = max(0.0, min(f_star, max_kelly))

    path = (
        f"p={p:.3f}, b=({tp:.2f}-{entry:.2f})/({entry:.2f}-{sl:.2f})={b:.3f}, "
        f"f*=({p:.3f}*{b:.3f}-(1-{p:.3f}))/{b:.3f}={f_star:.4f}, "
        f"capped=min({f_star:.4f}, {max_kelly})={f_capped:.4f}"
    )
    return f_capped, path
