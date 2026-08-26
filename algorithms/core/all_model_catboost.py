import os
import pickle
import sys
from pathlib import Path

import pandas as pd
import numpy as np
import warnings
import statsmodels.api as sm
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.linear_model import LinearRegression
from sklearn.svm import SVR
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor
from lightgbm import LGBMRegressor
from catboost import CatBoostRegressor


pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.max_colwidth', 50)
pd.set_option('display.width', 1000)

independent_vars = ['体长', '体高']
dependent_var = '体重'

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parents[1]
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from algorithms.config.paths import (  # noqa: E402
    CATBOOST_FULL_MODEL_PATH,
    MODEL_DIR,
    ensure_dir,
)


result_dir = str(MODEL_DIR / "fishweight_results_catboost")
cb_full_model_path = str(CATBOOST_FULL_MODEL_PATH)
ensure_dir(Path(result_dir))
ensure_dir(MODEL_DIR)
warnings.filterwarnings("ignore")

DEFAULT_TRAINING_XLSX = str(MODEL_DIR / "data.xlsx")


def train_and_evaluate_models():
    all_data = pd.read_excel(DEFAULT_TRAINING_XLSX)
    for var in independent_vars + [dependent_var]:
        if all_data[var].isnull().sum() > 0:
            all_data[var] = all_data[var].fillna(all_data[var].mean())

    X = all_data[independent_vars]
    y = all_data[dependent_var]
    kf = KFold(n_splits=10, shuffle=True, random_state=42)

    print("\n" + "=" * 80)
    print("📌 基础预测法：直接使用体重平均值作为预测值")
    print("=" * 80)
    mean_weight = y.mean()
    print(f"全体重平均值：{mean_weight:.4f}")

    all_data['预测体重(平均值)'] = mean_weight
    all_data['误差百分比(%)'] = np.abs((all_data[dependent_var] - all_data['预测体重(平均值)']) / all_data[dependent_var]) * 100

    print(f"\n整体平均误差百分比：{all_data['误差百分比(%)'].mean():.2f}%")
    print(f"最大误差百分比：{all_data['误差百分比(%)'].max():.2f}%")
    print(f"最小误差百分比：{all_data['误差百分比(%)'].min():.2f}%")
    print("=" * 80 + "\n")

    
    models = {
        "多元线性回归": LinearRegression(),
        "SVR": SVR(),
        "决策树回归": DecisionTreeRegressor(random_state=42),
        "随机森林回归": RandomForestRegressor(random_state=42),
        "XGBoost": XGBRegressor(random_state=42, objective='reg:squarederror'),
        "LightGBM": LGBMRegressor(random_state=42, verbose=-1),
        "CatBoost": CatBoostRegressor(random_state=42, verbose=0)
    }

    summary_results = []

    for model_name, model in models.items():
        model_folder = os.path.join(result_dir, model_name)
        os.makedirs(model_folder, exist_ok=True)

        txt_path = os.path.join(model_folder, f"result_10fold_{model_name}.txt")
        print(f"正在保存：{txt_path}")

        cv_metrics = {
            'fold': [], 'mse': [], 'rmse': [], 'mae': [], 'mean_error_rate(%)': [],
            'r2': [], 'adj_r2': [], 'dw_value': []
        }

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"【{model_name} - 10折交叉验证详细结果】\n")
            f.write("=" * 60 + "\n")
            f.write(f"总样本数：{len(all_data)}\n")
            f.write(f"每折训练集：{int(len(all_data)*0.9)}，验证集：{int(len(all_data)*0.1)}\n\n")

            for fold_idx, (train_idx, val_idx) in enumerate(kf.split(X), 1):
                X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
                y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
                X_original_train = all_data.iloc[train_idx][independent_vars]
                X_original_val = all_data.iloc[val_idx][independent_vars]

                
                scaler = StandardScaler()
                X_train_scaled = scaler.fit_transform(X_train)
                X_val_scaled = scaler.transform(X_val)

                model.fit(X_train_scaled, y_train)
                y_pred_train = model.predict(X_train_scaled)
                y_pred_val = model.predict(X_val_scaled)

                
                mse_val = mean_squared_error(y_val, y_pred_val)
                rmse_val = np.sqrt(mse_val)
                mae_val = mean_absolute_error(y_val, y_pred_val)
                mer_val = np.mean(np.abs((y_val - y_pred_val) / y_val)) * 100

                ss_res = np.sum((y_val - y_pred_val) ** 2)
                ss_tot = np.sum((y_val - y_val.mean()) ** 2)
                r2_val = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
                adj_r2_val = 1 - (1 - r2_val) * (len(y_val) - 1) / (len(y_val) - X_train.shape[1] - 1) if len(y_val) > X_train.shape[1]+1 else r2_val

                
                try:
                    X_train_sm = sm.add_constant(X_train_scaled)
                    sm_model = sm.OLS(y_train, X_train_sm).fit()
                    dw_val = sm.stats.stattools.durbin_watson(sm_model.resid)
                except:
                    dw_val = 0.0

                cv_metrics['fold'].append(fold_idx)
                cv_metrics['mse'].append(mse_val)
                cv_metrics['rmse'].append(rmse_val)
                cv_metrics['mae'].append(mae_val)
                cv_metrics['mean_error_rate(%)'].append(mer_val)
                cv_metrics['r2'].append(r2_val)
                cv_metrics['adj_r2'].append(adj_r2_val)
                cv_metrics['dw_value'].append(dw_val)

                f.write(f"第 {fold_idx} 折\n")
                f.write("-" * 40 + "\n")
                f.write(f"验证集 MSE: {mse_val:.6f}\n")
                f.write(f"验证集 RMSE: {rmse_val:.6f}\n")
                f.write(f"验证集 MAE: {mae_val:.6f}\n")
                f.write(f"平均误差率: {mer_val:.2f}%\n")
                f.write(f"R²: {r2_val:.4f}\n")
                f.write(f"调整后 R²: {adj_r2_val:.4f}\n")
                f.write(f"D-W: {dw_val:.4f}\n\n")

                if fold_idx == 1:
                    train_df = pd.DataFrame({
                        '体长': X_original_train['体长'].values,
                        '体高': X_original_train['体高'].values,
                        '实际体重': y_train.values,
                        '预测体重': y_pred_train,
                        '误差百分比': np.abs((y_train - y_pred_train) / y_train) * 100,
                        '数据类型': '训练集'
                    })
                    val_df = pd.DataFrame({
                        '体长': X_original_val['体长'].values,
                        '体高': X_original_val['体高'].values,
                        '实际体重': y_val.values,
                        '预测体重': y_pred_val,
                        '误差百分比': np.abs((y_val - y_pred_val) / y_val) * 100,
                        '数据类型': '验证集'
                    })
                    fold1_result = pd.concat([train_df, val_df], ignore_index=True)
                    save_path = os.path.join(model_folder, f"第一折详细结果_{model_name}.xlsx")
                    fold1_result.to_excel(save_path, index=False)
                    print(f"✅ 已保存 {model_name} 第一折详细表")

            df = pd.DataFrame(cv_metrics)
            f.write("\n【10折汇总】\n")
            f.write("=" * 60 + "\n")
            f.write(f"平均 RMSE: {df['rmse'].mean():.6f} ± {df['rmse'].std():.6f}\n")
            f.write(f"平均误差率: {df['mean_error_rate(%)'].mean():.2f} ± {df['mean_error_rate(%)'].std():.2f}%\n")
            f.write(f"平均 R²: {df['r2'].mean():.4f} ± {df['r2'].std():.4f}\n")

        summary_results.append({
            "模型": model_name,
            "RMSE": f"{df['rmse'].mean():.6f} ± {df['rmse'].std():.6f}",
            "MAPE(%)": f"{df['mean_error_rate(%)'].mean():.2f} ± {df['mean_error_rate(%)'].std():.2f}",
            "R²": f"{df['r2'].mean():.4f} ± {df['r2'].std():.4f}"
        })

    summary_df = pd.DataFrame(summary_results)
    summary_csv = os.path.join(result_dir, "all_model_summary.csv")
    summary_xlsx = os.path.join(result_dir, "all_model_summary.xlsx")
    summary_df.to_csv(summary_csv, index=False, encoding='utf-8-sig')
    summary_df.to_excel(summary_xlsx, index=False)

    print("\n✅ 全部模型十折交叉验证完成！文件已按模型分类保存到独立文件夹")
    return summary_df


def train_full_catboost_model():

    all_data = pd.read_excel(DEFAULT_TRAINING_XLSX)
    for var in independent_vars + [dependent_var]:
        if all_data[var].isnull().sum() > 0:
            all_data[var] = all_data[var].fillna(all_data[var].mean())

    X_full = all_data[independent_vars].values
    y_full = all_data[dependent_var].values

    scaler = StandardScaler()
    X_full_scaled = scaler.fit_transform(X_full)

    full_model = CatBoostRegressor(random_state=42, verbose=0)
    full_model.fit(X_full_scaled, y_full)

    
    save_package = {
        "model": full_model,
        "scaler": scaler
    }
    with open(cb_full_model_path, "wb") as f:
        pickle.dump(save_package, f)
    print(f"✅ CatBoost全量模型训练完成,模型+缩放器已保存至：{cb_full_model_path}")

  
    y_pred = full_model.predict(X_full_scaled)
    mse = mean_squared_error(y_full, y_pred)
    rmse = np.sqrt(mse)
    mae = mean_absolute_error(y_full, y_pred)
    mape = np.mean(np.abs((y_full - y_pred) / y_full)) * 100

    print(f"\n📊 CatBoost全量模型整体评估指标：")
    print(f"MSE: {mse:.6f}")
    print(f"RMSE: {rmse:.6f}")
    print(f"MAE: {mae:.6f}")
    print(f"MAPE: {mape:.2f}%")

    return full_model, scaler


def measure(image_path: str, body_length: float, body_height: float) -> float:
    """
    加载保存好的CatBoost全量模型,单条样本推理预测体重
    Args:
        image_path: 图像路径（预留扩展字段，暂不参与计算）
        body_length: 体长 cm
        body_height: 体高 cm
    Returns:
        预测体重(g),保留2位小数
    """
    with open(cb_full_model_path, "rb") as f:
        load_package = pickle.load(f)
    model = load_package["model"]
    scaler = load_package["scaler"]

    input_arr = np.array([[body_length, body_height]])
    input_scaled = scaler.transform(input_arr)
    pred_weight = model.predict(input_scaled)[0]
    return round(float(pred_weight), 2)


if __name__ == "__main__":
    #train_and_evaluate_models()
    
    #full_cb_model, full_scaler = train_full_catboost_model()

    print("\n" + "=" * 60)
    print("🔍 调用全量CatBoost模型推理测试")
    test_img = "test_fish.jpg"
    test_len = 25.6
    test_hgt = 8.2
    pred_weight = measure(test_img, test_len, test_hgt)
    print(f"输入 -> 体长：{test_len} cm, 体高:{test_hgt} cm")
    print(f"预测体重 -> {pred_weight} g")
