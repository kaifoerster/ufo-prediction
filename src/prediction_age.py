import logging

import visualizations
import dataset

import shap
import pandas as pd
import numpy as np
from sklearn import utils, metrics, model_selection

logger = logging.getLogger(__name__)


class AgePredictor:

    def __init__(self, model, df, test_training_split, preprocessing_stages=[], hyperparameter_tuning=False) -> None:
        self.model = model
        self.df = df
        self.test_training_split = test_training_split
        self.preprocessing_stages = preprocessing_stages
        self.hyperparameter_tuning = hyperparameter_tuning

        self.X_train = None
        self.y_train = None
        self.X_test = None
        self.y_test = None
        self.y_predict = None
        self.shap_explainer = None
        self.shap_values = None

        self._preprocess()
        self._train()


    def _preprocess(self):
        logger.info(f'Dataset length: {len(self.df)}')

        # Test & Training Split
        df_train, df_test = self.test_training_split(self.df)
        logger.info(f'Test dataset length: {len(df_test)}')
        logger.info(f'Training dataset length: {len(df_train)}')

        # The standard deviation in the test set gives us an indication of a baseline. We want to be able to be substantially below that value.
        logger.info(
            f"Standard deviation of test set: {df_test[dataset.AGE_ATTRIBUTE].std()}")

        # Preprocessing & Cleaning
        for func in self.preprocessing_stages:
            df_train = func(df_train)
            df_test = func(df_test)

        df_train = utils.shuffle(df_train, random_state=0)
        df_test = utils.shuffle(df_test, random_state=0)

        self.X_train = df_train.drop(columns=dataset.AUX_VARS+[dataset.AGE_ATTRIBUTE])
        self.y_train = df_train[[dataset.AGE_ATTRIBUTE]]

        self.X_test = df_test.drop(columns=dataset.AUX_VARS+[dataset.AGE_ATTRIBUTE])
        self.y_test = df_test[[dataset.AGE_ATTRIBUTE]]


    def _train(self):
        if self.hyperparameter_tuning:
            params = tune_hyperparameter(self.model, self.X_train, self.y_train)
            self.model.set_params(**params)

        # Training & Predicting
        eval_set = [(self.X_train, self.y_train), (self.X_test, self.y_test)]
        self.model.fit(self.X_train, self.y_train) #, verbose=False, eval_set=eval_set)
        self.y_predict = self.model.predict(self.X_test)


    def evaluate_classification(self):
        self.print_classification_report()
        visualizations.plot_histogram(self.y_test, self.y_predict)
        visualizations.plot_confusion_matrix(self.y_test, self.y_predict, classes=[0, 1, 2, 3, 4, 5])


    def evaluate_regression(self):
        self.print_model_error()
        visualizations.plot_histogram(self.y_test, self.y_predict, age_bins=[
                    1850, 1915, 1945, 1965, 1980, 2000, 2025])
        visualizations.plot_grid(self.y_test, self.y_predict)


    def calculate_SHAP_values(self):
        self.shap_explainer = shap.TreeExplainer(self.model)
        self.shap_values = self.shap_explainer.shap_values(self.X_train)
        return self.shap_values


    def SHAP_analysis(self):
        self.calculate_SHAP_values()
        shap.summary_plot(self.shap_values, self.X_train)
        shap.summary_plot(self.shap_values, self.X_train, plot_type='bar')


    def normalized_feature_importance(self):
        # Calculate feature importance based on SHAP values
        self.calculate_SHAP_values()

        avg_shap_value = np.abs(self.shap_values).mean(0)
        normalized_shap_value = avg_shap_value / sum(avg_shap_value)
        feature_names = self.X_train.columns

        feature_importance = pd.DataFrame(
            {'feature': feature_names, 'normalized_importance': normalized_shap_value})
        return feature_importance.sort_values(by=['normalized_importance'], ascending=False)


    def feature_selection(self):
        if 'feature_noise' not in self.X_train.columns:
            raise Exception(
                "feature_noise column missing. Please add 'add_noise_feature' preprocessing step before doing feature selection.")

        df_fi = self.normalized_feature_importance()

        # Dismiss features which have a lower impact than the noise feature
        significance_level = 0.005
        noise_feature_importance = df_fi.query("feature=='feature_noise'").normalized_importance.values[0]
        threshold = df_fi.normalized_importance > noise_feature_importance + significance_level

        selected_features = df_fi[threshold]
        # remove noise feature from this list
        excluded_features = df_fi[~threshold].iloc[1:]

        print(f'{len(excluded_features)} of {len(self.X_train.columns)-1} features have been excluded:')
        print(excluded_features)

        return selected_features, excluded_features


    def feature_dependence_plot(self, feature1, feature2, low_percentile=0, high_percentile=100, transparency=1):
        self.calculate_SHAP_values()
        shap.dependence_plot(feature1, self.shap_values, self.X_train, interaction_index=feature2,
                             xmin=f"percentile({low_percentile})", xmax=f"percentile({high_percentile})", alpha=transparency)


    def neighborhood_feature_importance(self):
        df_fi = self.normalized_feature_importance()
        df_neighborhood_fi = df_fi.loc[df_fi['feature'].str.contains('within_buffer')]
        return round(sum(df_neighborhood_fi.normalized_importance), 2)


    def print_feature_importance(self):
        feature_accuracy_contribution = self.model.get_booster().get_score(importance_type="gain")
        feature_importance = pd.DataFrame({'importance': feature_accuracy_contribution})
        feature_importance.sort_values(by=['importance'], ascending=False, inplace=True)
        print(feature_importance.head(15))


    def print_model_error(self):
        print('MAE: {} y'.format(metrics.mean_absolute_error(self.y_test, self.y_predict)))
        print('RMSE: {} y'.format(np.sqrt(metrics.mean_squared_error(self.y_test, self.y_predict))))
        print('R2: {}'.format(metrics.r2_score(self.y_test, self.y_predict)))


    def print_classification_report(self):
        print(metrics.classification_report(self.y_test, self.y_predict))


def tune_hyperparameter(model, X, y):
    params = {
        'max_depth': [1, 3, 6, 10], # try ada trees
        'learning_rate': [0.05, 0.1, 0.3],
        'n_estimators': [100, 500, 1000],
        'colsample_bytree': [0.3, 0.5, 0.7],
        'subsample': [0.7, 1.0],
    }

    # https://scikit-learn.org/stable/modules/model_evaluation.html#scoring-parameter
    clf = model_selection.GridSearchCV(estimator=model,
                                       param_grid=params,
                                       scoring='neg_root_mean_squared_error',
                                       verbose=1)
    clf.fit(X, y)
    print("Best parameters: ", clf.best_params_)
    print("Lowest RMSE: ", np.sqrt(-clf.best_score_))

    tuning_results = pd.concat([pd.DataFrame(clf.cv_results_["params"]), pd.DataFrame(
        clf.cv_results_["mean_test_score"], columns=["Accuracy"])], axis=1)
    tuning_results.to_csv('hyperparameter-tuning-results.csv', sep='\t')
    print('All hyperparameter tuning results:\n', tuning_results)

    return clf.best_params_

