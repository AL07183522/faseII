"""Modelo Random Forest para detección de fraude transaccional.

Entrena un RandomForestClassifier sobre datos/datos_fraude.csv con partición
estratificada 70/30 y lo evalúa con métricas adecuadas para clases
desbalanceadas y costos asimétricos (FN = $1,500 USD, FP = $25 USD).

Uso (desde la raíz del repositorio):
    .venv/bin/python fuentes/modelo_random_forest.py
"""

import json
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    average_precision_score,
    confusion_matrix,
    fbeta_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split

RAIZ = Path(__file__).resolve().parent.parent
RUTA_DATOS = RAIZ / "datos" / "datos_fraude.csv"
RUTA_METRICAS = RAIZ / "datos" / "metricas_random_forest.json"
RUTA_MODELO = RAIZ / "modelos" / "modelo_random_forest.joblib"
DIR_IMAGENES = RAIZ / "imagenes"

OBJETIVO = "es_fraude"
SEMILLA = 42
COSTO_FN = 1500  # Fraude no detectado
COSTO_FP = 25  # Falsa alarma


def crear_modelo():
    return RandomForestClassifier(
        n_estimators=150,
        max_depth=7,
        min_samples_split=10,
        min_samples_leaf=4,
        class_weight="balanced",
        random_state=SEMILLA,
        n_jobs=-1,
    )


def costo_total(y_real, y_pred):
    tn, fp, fn, tp = confusion_matrix(y_real, y_pred, labels=[0, 1]).ravel()
    return COSTO_FN * fn + COSTO_FP * fp


def evaluar(y_real, probabilidades, umbral):
    y_pred = (probabilidades >= umbral).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_real, y_pred, labels=[0, 1]).ravel()
    return {
        "umbral": round(float(umbral), 4),
        "recall": round(recall_score(y_real, y_pred), 4),
        "precision": round(precision_score(y_real, y_pred, zero_division=0), 4),
        "f2": round(fbeta_score(y_real, y_pred, beta=2), 4),
        "matriz_confusion": {"TN": int(tn), "FP": int(fp), "FN": int(fn), "TP": int(tp)},
        "costo_total_usd": int(COSTO_FN * fn + COSTO_FP * fp),
    }


def buscar_umbral_optimo(y_real, probabilidades):
    """Umbral que minimiza el costo total sobre las probabilidades dadas."""
    umbrales = np.linspace(0.01, 0.99, 99)
    costos = [costo_total(y_real, (probabilidades >= u).astype(int)) for u in umbrales]
    return float(umbrales[int(np.argmin(costos))])


def graficar_matriz(y_real, probabilidades, umbral, nombre_archivo):
    y_pred = (probabilidades >= umbral).astype(int)
    fig, ax = plt.subplots(figsize=(5, 4))
    ConfusionMatrixDisplay.from_predictions(
        y_real, y_pred, display_labels=["Legítima", "Fraude"], cmap="Blues", ax=ax
    )
    ax.set_title(f"Random Forest — umbral {umbral:.2f}")
    fig.tight_layout()
    fig.savefig(DIR_IMAGENES / nombre_archivo, dpi=150)
    plt.close(fig)


def graficar_importancias(modelo, columnas):
    importancias = pd.Series(modelo.feature_importances_, index=columnas).sort_values()
    fig, ax = plt.subplots(figsize=(7, 5))
    importancias.plot.barh(ax=ax, color="#2b6cb0")
    ax.set_title("Random Forest — importancia de variables")
    ax.set_xlabel("Importancia (reducción media de impureza)")
    fig.tight_layout()
    fig.savefig(DIR_IMAGENES / "rf_importancia_variables.png", dpi=150)
    plt.close(fig)
    return importancias.sort_values(ascending=False)


def main():
    datos = pd.read_csv(RUTA_DATOS)
    X = datos.drop(columns=OBJETIVO)
    y = datos[OBJETIVO]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.30, stratify=y, random_state=SEMILLA
    )
    print(f"Entrenamiento: {len(X_train)} registros ({y_train.sum()} fraudes)")
    print(f"Prueba:        {len(X_test)} registros ({y_test.sum()} fraudes)")

    # El umbral se elige con predicciones fuera de pliegue del entrenamiento,
    # para no usar el conjunto de prueba en ninguna decisión del modelo.
    validacion = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    prob_oof = cross_val_predict(
        crear_modelo(), X_train, y_train, cv=validacion, method="predict_proba"
    )[:, 1]
    umbral_optimo = buscar_umbral_optimo(y_train, prob_oof)

    modelo = crear_modelo().fit(X_train, y_train)
    prob_test = modelo.predict_proba(X_test)[:, 1]

    resultados = {
        "modelo": "RandomForestClassifier",
        "hiperparametros": modelo.get_params(),
        "particion": {
            "entrenamiento": len(X_train),
            "prueba": len(X_test),
            "fraudes_prueba": int(y_test.sum()),
        },
        "roc_auc": round(roc_auc_score(y_test, prob_test), 4),
        "pr_auc": round(average_precision_score(y_test, prob_test), 4),
        "umbral_0_5": evaluar(y_test, prob_test, 0.5),
        "umbral_optimo_costo": evaluar(y_test, prob_test, umbral_optimo),
    }

    graficar_matriz(y_test, prob_test, 0.5, "rf_matriz_confusion_umbral_0_5.png")
    graficar_matriz(y_test, prob_test, umbral_optimo, "rf_matriz_confusion_umbral_optimo.png")
    importancias = graficar_importancias(modelo, X.columns)
    resultados["importancia_variables"] = importancias.round(4).to_dict()

    RUTA_METRICAS.write_text(json.dumps(resultados, indent=2, ensure_ascii=False))

    # Artefacto que consume la API: modelo, umbral de decisión y orden de columnas.
    RUTA_MODELO.parent.mkdir(exist_ok=True)
    joblib.dump(
        {
            "modelo": modelo,
            "umbral": umbral_optimo,
            "columnas": list(X.columns),
            "metricas": {
                "roc_auc": resultados["roc_auc"],
                "pr_auc": resultados["pr_auc"],
                "umbral_optimo_costo": resultados["umbral_optimo_costo"],
            },
        },
        RUTA_MODELO,
    )
    print(f"Modelo guardado en {RUTA_MODELO.relative_to(RAIZ)}")

    print(f"\nROC-AUC: {resultados['roc_auc']}   PR-AUC: {resultados['pr_auc']}")
    for clave in ("umbral_0_5", "umbral_optimo_costo"):
        r = resultados[clave]
        print(
            f"\n[{clave}] umbral={r['umbral']}  recall={r['recall']}  "
            f"precision={r['precision']}  F2={r['f2']}"
        )
        print(f"  {r['matriz_confusion']}  costo total=${r['costo_total_usd']:,} USD")
    print("\nTop 5 variables:")
    print(importancias.head(5).round(4).to_string())


if __name__ == "__main__":
    main()
