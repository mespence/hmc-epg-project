

import os
import importlib.util
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import optuna
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.colors as mcolors 
import distinctipy
from sklearn.model_selection import KFold
from sklearn.metrics import classification_report, accuracy_score
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from sklearn.metrics import (
    precision_recall_fscore_support, confusion_matrix, 
    ConfusionMatrixDisplay, accuracy_score, f1_score
)

from data_loader import import_data


class DataImport:
    """
    A class for importing and organizing labeled time-series datasets from CSV or Parquet files.

    Attributes:
    -----------
    df_list : list[pd.DataFrame]
        A list of preprocessed DataFrames, each storing its source filename in df.attrs["file"].
    random_state : int
        Random seed used for reproducibility in KFold shuffling.
    cross_val_iter : list[tuple]
        A list of (train_index, test_index) tuples for K-fold cross-validation splits.
    """
    def __init__(self, data_path, filetype: str, exclude=[], folds=5):
        """
        Initializes the DataImport class.

        Parameters:
        -----------
        data_path : str or Path
            Directory containing the data files (.csv or .parquet).
        filetype : str
            File extension to filter files by (e.g., ".csv" or ".parquet").
        exclude : list[str], optional
            Substrings; any file whose name contains one will be excluded.
        folds : int, optional
            Number of folds to use for K-fold cross-validation (default is 5).
        """
        self.df_list = import_data(data_path, filetype, exclude)
        self.random_state = 42
        kf = KFold(n_splits=folds, random_state=self.random_state, shuffle=True)
        self.cross_val_iter = list(kf.split(self.df_list))

    def process_df(self,  df: pd.DataFrame):
        labels = df["labels"].values
        probe_indices = self.leak_probe_finder(labels)
        filename_base = Path(df.attrs["file"]).stem

        probes = []
        names = []

        for i, (start, end) in enumerate(probe_indices):
            probe = df.iloc[start:end]
            probe.attrs["file"] = df.attrs["file"]
            probe.attrs["probe_index"] = i
            probes.append(probe)
            names.append(f"{filename_base}_{i}")

        return probes, names

    
    def get_probes(self, dfs: list[pd.DataFrame]) -> tuple[list[pd.DataFrame], list[str]]:
        """
        Extract probing segments from a list of DataFrames in parllel.

        Returns:
            all_probes: List of individual probe DataFrames
            all_probe_names: List of names (e.g., file_0, file_1, ...)
        """
        all_probes = []
        all_probe_names = []

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(self.process_df, df) for df in dfs]
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Getting probes",
                position=2,
                leave=False
            ):
                probes, names = future.result()
                all_probes.extend(probes)
                all_probe_names.extend(names)

        return all_probes, all_probe_names

    
    def leak_probe_finder(self, labels):
        """
        Returns (start, end) index tuples for contiguous probe segments
        with labels not in NON_PROBING_LABELS.
        """
        NON_PROBING_LABELS = {"N", "Z"}

        upper_labels = np.char.upper(labels.astype(str))
        mask = ~np.isin(upper_labels, list(NON_PROBING_LABELS))
        probe_indices = np.where(mask)[0]

        if probe_indices.size == 0:
            return []

        breaks = np.where(np.diff(probe_indices) > 1)[0]
        segment_starts = np.insert(probe_indices[breaks + 1], 0, probe_indices[0])
        segment_ends = np.append(probe_indices[breaks], probe_indices[-1])

        return list(zip(segment_starts, segment_ends))

def dynamic_importer(path):
    spec = importlib.util.spec_from_file_location("model", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import from {path}")
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    return model




def optuna_objective(data, args, trial, **kwargs):
    msg_bar = tqdm(total=0, position=1, bar_format="{desc}", leave=False)

    def show_msg(msg: str):
        msg_bar.set_description_str(msg)

    def clear_msg():
        msg_bar.set_description_str("")
    
    for fold, (train_index, test_index) in enumerate(data.cross_val_iter):
        show_msg(f"Initializing Trial {trial.number} Fold {fold}")
        train_data = [data.df_list[i] for i in train_index]
        test_data = [data.df_list[i] for i in test_index]
        train_data, _ = data.get_probes(train_data)
        test_data, _ = data.get_probes(test_data)
        clear_msg()

        show_msg(f"Training Trial {trial.number} Fold {fold}")
        model_import = dynamic_importer(args.model_path)
        model = model_import.Model(trial = trial, **kwargs)
        model.train(train_data, test_data, fold)
        clear_msg()

        show_msg(f"Predicting Trial {trial.number} Fold {fold}")
        predicted_labels = model.predict(test_data)
        clear_msg()

        labels_true = np.concatenate([df["labels"].values for df in test_data])
        labels_pred = np.concatenate(predicted_labels)

    show_msg(f"Evaluating Trial {trial.number}...")
    weighted_f1 = f1_score(labels_true, labels_pred, average="weighted")
    macro_f1 = f1_score(labels_true, labels_pred, average="macro")
    micro_f1 = f1_score(labels_true, labels_pred, average="micro")
    clear_msg()

    tqdm.write(f"Trial {trial.number} F1s - Weighted: {weighted_f1:.4f} | Macro: {macro_f1:.4f} | Micro: {micro_f1:.4f}")
    tqdm.write("")

    with open(f"{args.model_name}_optuna.txt", "a") as f:
        print(trial.datetime_start, trial.number, trial.params, weighted_f1, file=f)

    return weighted_f1

def plot_labels(time, voltage, true_labels, pred_labels, probs = None):
    """
    plot_labels produced a matplotlib figure containing three subplots
        that visualize a waveform along with the true and predicted labels
    Input:
        time: a series of time values
        voltage: a time series of voltage values from the waveform
        true_labels: a time series of the true label for each time point
        pred_labels: a time series of the predicted labels for each time point
    Output:
        (fig, axs): a tuple
    """


    def generate_label_colors(labels):
        labels = sorted(set(labels))
        colors = distinctipy.get_colors(len(labels))
        hex_colors = [distinctipy.get_hex(color) for color in colors]
        return dict(zip(labels, hex_colors))
    
    unique_labels = ['B', 'B2', 'B4', 'C', 'CG', 'D', 'DG', 'F', 'F1', 'F2', 'F3', 'F4', 'FB', 'G', 'N', 'P', 'Z']
    label_to_color = generate_label_colors(unique_labels)

    fig, axs = plt.subplots(3, 1, sharex = True)
    recording = 1
    fill_min, fill_max = voltage.min(), voltage.max()
    
    # First plot will be the true labels
    axs[0].plot(time, voltage, color = "black")
    for label, color in label_to_color.items():
        fill = axs[0].fill_between(time, fill_min, fill_max, 
                where = (true_labels == label), color=color, alpha = 0.5)
        fill.set_label(label)
    axs[0].legend(bbox_to_anchor=(0.5, 1), 
                  bbox_transform=fig.transFigure, loc="upper center", ncol=9)
    axs[0].set_title("True Labels")
    # Second plot will be the predicted labels
    axs[1].plot(time, voltage, color = "black")
    for label, color in label_to_color.items():
        axs[1].fill_between(time, fill_min, fill_max, 
                where = (pred_labels == label), color=color, alpha = 0.5)
    axs[1].set_title("Predicted Labels")
    # Third plot will be marked where there is a difference between the two
    axs[2].plot(time, voltage, color = "black")
    axs[2].fill_between(time, fill_min, fill_max, 
            where = (pred_labels != true_labels), color = "gray", alpha = 0.5)
    axs[2].set_title("Incorrect Labels")
    # Axes titles and such
    fig.supxlabel("Time (s)")
    fig.supylabel("Volts")
    fig.tight_layout()
    return fig

def generate_report(test_data, predicted_labels, test_names, save_path, model_name, fold):
    # Flatten everything
    labels_true = []
    labels_pred = []
    for df, preds in zip(test_data, predicted_labels):
        labels_true.extend(df["labels"].values)
        labels_pred.extend(preds)

    # Make sure we have a place to save everything
    if not os.path.isdir(save_path):
        os.mkdir(save_path)
        

    # precision et. al
    labels = sorted(np.unique(labels_true))
    precision, recall, fscore, _ = precision_recall_fscore_support(
        labels_true, labels_pred, labels=labels, average=None, zero_division=0
    )
    metrics = {}
    for label, p, r, f in zip(labels, precision, recall, fscore):
        metrics[f"{label}_precision"] = p
        metrics[f"{label}_recall"] = r
        metrics[f"{label}_fscore"] = f

    out_dataframe = pd.DataFrame([metrics])

    # accuracy
    accuracy = accuracy_score(labels_true, labels_pred)
    out_dataframe["accuracy"] = accuracy

    # confusion matrix
    ConfusionMatrixDisplay.from_predictions(labels_true, labels_pred, \
                                            normalize = 'true')
    plt.savefig(rf"{save_path}/{model_name}_ConfusionMatrix_Fold{fold}.png")

    # difference plots
    base_name = Path(model_name).name
    for df, preds, name in zip(test_data, predicted_labels, test_names):
        fig = plot_labels(
            df["time"],
            df["voltage"],
            df["labels"].values,
            np.asarray(preds)
        )
        file_stem = Path(name).stem
        fig_path = Path(save_path) / "difference_plots" / f"{base_name}_{file_stem}_Fold{fold}.png"
        fig_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(fig_path)
        plt.close(fig)

    print(f"Fold {fold} Overall Accuracy: {accuracy}")
    return labels_true, labels_pred, out_dataframe

def main():
    parser = argparse.ArgumentParser(
        prog = "Model Performance Evaluator",
        description = "This program takes in EPG data and a \
                        labeler program, trains it, and then \
                        generates statistics and figures to \
                        characterize the model's performance."
    )
    parser.add_argument("--data_path", type = str, required = True)
    parser.add_argument("--model_path", type = str, required = True)
    parser.add_argument("--save_path", type = str, required = True)
    parser.add_argument("--model_name", type = str, required = True)
    #parser.add_argument()
    #parser.add_argument("--augment", action="store_true")
    #parser.add_argument("--post_process", type = str, required = False) # can either be s/smooth or viterbi/m
    parser.add_argument("--epochs", type = int, required=False)
    parser.add_argument("--optuna", action="store_true")
    parser.add_argument("--attention", action="store_true") # can only be used with UNet 
    args = parser.parse_args()

    EXCLUDE = {
        "a01", "a02", "a03", "a10", "a15",
        "b01", "b02", "b04", "b07", "b12", "b188", "b202", "b206", "b208",
        "c046", "c07", "c09", "c10",
        "d01", "d03", "d056", "d058", "d12",
    }

    data = DataImport(args.data_path, filetype = ".parquet", exclude=EXCLUDE, folds = 5)

    if args.optuna:
        def progress_bar_callback(total_trials):
            pbar = tqdm(total=total_trials, desc="Optuna Trials", position=0)
            return lambda s, t: pbar.update(1)
        
        trial_count = 25

        study = optuna.create_study(study_name=f"{args.model_name}_hyperparameter_tuning", direction='maximize')

        kwargs = dict()
        if "unet" in args.model_path:
            if args.attention:
                # expected f1: 0.7402015172114621
                kwargs['bottleneck_type'] = 'windowed_attention'
                kwargs = kwargs | {'epochs': 64, 'lr': 0.0005, 'dropout_rate': 1e-05, 'weight_decay': 1e-05, 'num_layers': 8, 'features': 64, 'transformer_window_size': 150, 'transformer_layers': 2}
                heads_per_channel = 32
                kwargs['transformer_nhead'] = max(kwargs['features'] // heads_per_channel, 1)
                kwargs['embed_dim'] = kwargs['features']
            else:
                study.enqueue_trial(
                    {
                        "epochs" : args.epochs,
                        "lr": 5e-4,
                        "dropout": 1e-6,
                        "weight_decay": 1e-6,
                        "num_layers": 6,
                        "features": 64,
                    }
                )

                # expected f1: 0.694895
                kwargs['bottleneck_type'] = 'block'
                kwargs = kwargs | {'epochs': args.epochs, 'lr': 0.0005, 'dropout_rate': 0.1, 'weight_decay': 1e-06, 'num_layers': 8, 'features': 32}

            if args.epochs:
                kwargs['epochs'] = args.epochs

        else:
            kwargs = {}

        study.optimize(
            lambda x : optuna_objective(data, args, x, **kwargs), 
            n_trials = trial_count, 
            show_progress_bar=False,
            callbacks=[progress_bar_callback(trial_count)] # add custom progress bar
        )

        print(study.best_params)
        optuna.visualization.matplotlib.plot_optimization_history(study)
        plt.savefig(f"{args.model_name}_hyper.png")
        return
    
    summary_data = []
    labels_true = []
    labels_pred = []
    for fold, (train_index, test_index) in enumerate(data.cross_val_iter):
        print(f"=== Evaluating Fold {fold} ===")
        train_data = [data.df_list[i] for i in train_index]
        test_data = [data.df_list[i] for i in test_index]
        train_data, _ = data.get_probes(train_data)
        test_data, test_names = data.get_probes(test_data)

        model_import = dynamic_importer(args.model_path)

        kwargs = dict()
        if "unet" in args.model_path:
            if args.attention:
                # expected f1: 0.7402015172114621
                kwargs['bottleneck_type'] = 'windowed_attention'
                kwargs = kwargs | {'epochs': 64, 'lr': 0.0005, 'dropout_rate': 1e-05, 'weight_decay': 1e-06, 'num_layers': 8, 'features': 32, 'transformer_window_size': 150, 'transformer_layers': 2}
                heads_per_channel = 32
                kwargs['transformer_nhead'] = max(kwargs['features'] // heads_per_channel, 1)
                kwargs['embed_dim'] = kwargs['features']
            else:
                # expected f1: 0.694895
                kwargs['bottleneck_type'] = 'block'
                kwargs = kwargs | {'epochs': 64, 'lr': 0.0005, 'dropout_rate': 0.1, 'weight_decay': 1e-06, 'num_layers': 8, 'features': 32}

            if args.epochs:
                kwargs['epochs'] = args.epochs

        model = model_import.Model(save_path = args.save_path, **kwargs)

        print("Training Model...")
        model.train(train_data)

        print("Evaluating Model...")
        predicted_labels = model.predict(test_data)

        print("Generating Report...")
        true, pred, stats = generate_report(test_data, predicted_labels, test_names, args.save_path, args.model_name, fold)
        print("Report generated.")
        summary_data.append(stats)
        labels_true.extend(true)
        labels_pred.extend(pred)
        
    out_summary_data = pd.concat(summary_data)

    # Calculate statistics across every dataset
    labels = sorted(np.unique(labels_true))
    all_precision, all_recall, all_fscore, _ = precision_recall_fscore_support(labels_true, labels_pred, 
                                                            labels=labels, average = None, zero_division=0)
    temp_dict = {"precision" : all_precision, 
                 "recall" : all_recall, 
                 "fscore" : all_fscore}
    out_dataframe = pd.DataFrame(temp_dict, index=labels).stack()
    out_dataframe.index = out_dataframe.index.map('{0[1]}_{0[0]}'.format)
    out_dataframe = out_dataframe.to_frame().T
    out_dataframe["accuracy"] = accuracy_score(labels_true, labels_pred)

    out_summary_data = pd.concat([out_summary_data, out_dataframe])
    out_summary_data.to_csv(f"{args.save_path}/{args.model_name}_SummaryStats.csv")

    overall = ConfusionMatrixDisplay.from_predictions(labels_true, labels_pred, \
                                            normalize = 'true')
    overall.plot().figure_.savefig(rf"{args.save_path}/{args.model_name}_OverallConfusionMatrix.png")

    all_data = pd.DataFrame({'labels_true': labels_true,
                             'labels_pred': labels_pred})
    all_data.to_csv(f"{args.save_path}/{args.model_name}_allpredictions.csv")

if __name__ == "__main__":
    main() 