"""Compare simple rankers on the first two valid RE1 repetitions."""

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from benchmark import read_case, selected_cases


def matrix(case, relative=False):
    x = np.asarray([service["features"] for service in case["services"]])
    if relative:
        ranks = np.stack([[(column < value).mean() for value in column] for column in x.T], axis=1)
        return np.hstack((x, ranks))
    return x


def evaluate(train_cases, test_cases, factory, relative=False):
    ranks = []
    for system in sorted({case["system"] for case in test_cases}):
        train = [case for case in train_cases if case["system"] != system]
        test = [case for case in test_cases if case["system"] == system]
        x = np.vstack([matrix(case, relative) for case in train])
        y = np.asarray([service["id"] == case["groundTruth"] for case in train for service in case["services"]])
        model = factory()
        model.fit(x, y)
        for case in test:
            scores = model.predict_proba(matrix(case, relative))[:, list(model.classes_).index(True)]
            order = np.argsort(-scores, kind="stable")
            truth = next(index for index, service in enumerate(case["services"]) if service["id"] == case["groundTruth"])
            ranks.append((case["id"], system, int(np.flatnonzero(order == truth)[0]) + 1))
    return ranks


def candidates():
    return {
        "original": (lambda: ExtraTreesClassifier(n_estimators=100, max_depth=5, min_samples_leaf=3, class_weight="balanced", random_state=7, n_jobs=-1), False),
        "extra_leaf1": (lambda: ExtraTreesClassifier(n_estimators=200, max_depth=8, min_samples_leaf=1, class_weight="balanced", random_state=7, n_jobs=-1), False),
        "extra_leaf2": (lambda: ExtraTreesClassifier(n_estimators=200, max_depth=8, min_samples_leaf=2, class_weight="balanced", random_state=7, n_jobs=-1), False),
        "extra_relative": (lambda: ExtraTreesClassifier(n_estimators=200, max_depth=5, min_samples_leaf=3, class_weight="balanced", random_state=7, n_jobs=-1), True),
        "forest": (lambda: RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=2, class_weight="balanced", random_state=7, n_jobs=-1), False),
        "forest_relative": (lambda: RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=2, class_weight="balanced", random_state=7, n_jobs=-1), True),
        "hist_boost": (lambda: HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=7, min_samples_leaf=10, class_weight="balanced", random_state=7), False),
        "logistic": (lambda: make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000, random_state=7)), False),
        "logistic_relative": (lambda: make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000, random_state=7)), True),
    }


def development():
    primary_rows = selected_cases()
    second_rows = selected_cases({row["case"] for row in primary_rows})
    primary = [read_case(row) for row in primary_rows]
    second = [read_case(row) for row in second_rows]
    for name, (factory, relative) in candidates().items():
        for label, training in (("primary", primary), ("two_reps", primary + second)):
            ranks = evaluate(training, second, factory, relative)
            top1 = sum(rank == 1 for _, _, rank in ranks)
            top3 = sum(rank <= 3 for _, _, rank in ranks)
            system = {key: sum(rank == 1 for _, held, rank in ranks if held == key) for key in ("ob", "ss", "tt")}
            print(name, label, top1, top3, system, flush=True)


if __name__ == "__main__":
    development()
