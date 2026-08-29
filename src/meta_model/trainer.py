import os
import sys
import logging
import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backtesting.engine import SIGINTBacktester

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Representative tickers of the global market (Tech, SP500, Macro)
TRAIN_TICKERS = ["QQQ", "SPY", "AAPL", "MSFT", "NVDA", "GLD", "TLT", "XOM", "JPM", "AMD"]

def generate_dataset() -> tuple[pd.DataFrame, pd.Series]:
    logger.info("Generating training dataset via Montecarlo (Backtester)...")
    # We use 5 years and daily entry to capture all regimes and generate thousands of samples.
    backtester = SIGINTBacktester(
        tickers=TRAIN_TICKERS,
        period="5y",
        entry_frequency="daily",
        seed=42,
        min_pop_filter=0.0, # Remove the base filter to get an unbiased statistical sample of the market
    )

    trades, metrics = backtester.run()

    features_list = []
    labels_list = []

    for t in trades:
        if t.features is None:
            continue
        features_list.append(t.features)
        # Meta-Labeling (López de Prado framework): 1 if the primary model (trade) was right, 0 if it failed.
        labels_list.append(1 if t.pnl_usd > 0 else 0)

    df = pd.DataFrame(features_list)
    y = pd.Series(labels_list)

    logger.info(f"Dataset generated: {len(df)} synthetic trades.")
    logger.info(f"Label Distribution (Win/Loss): \n{y.value_counts()}")
    return df, y

def train_model():
    X, y = generate_dataset()

    # 🔧 SRE FIX: Input impedance. RandomForest does not tolerate NaNs.
    # We clean up the sensor's background noise before training.
    if X.isnull().values.any():
        logger.warning("NaNs detected in the feature bus. Applying cleanup filter...")
        X = X.fillna(0)

    if len(X) < 100:
        logger.error("Aborting: Not enough silicon samples for a stable model.")
        return

    # Chronological split to avoid Look-ahead bias (Data Leakage)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    logger.info(f"Starting Discriminator (Meta-Model) training...")
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=5,
        min_samples_split=20,
        class_weight="balanced",
        random_state=1337,
        n_jobs=-1
    )
    model.fit(X_train, y_train)

    # OOS Evaluation (Out-of-Sample)
    y_pred = model.predict(X_test)
    logger.info(f"=== OOS METRICS ===\nAccuracy: {accuracy_score(y_test, y_pred):.2f}\n{classification_report(y_test, y_pred)}")

    # 🔧 SRE FIX: Persistence in a configurable Path for Docker/K8s volumes
    model_dir = os.getenv("MODEL_STORAGE_PATH", os.path.dirname(__file__))
    model_path = os.path.join(model_dir, "meta_model.pkl")

    joblib.dump(model, model_path)
    logger.info(f"💾 AI Firmware (Meta-Model) saved to: {model_path}")

if __name__ == "__main__":
    train_model()
