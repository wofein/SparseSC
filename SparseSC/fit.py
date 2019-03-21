""" Main public API providing a single call for fitting SC Models

Implements round-robin fitting of Sparse Synthetic Controls Model for DGP based analysis
"""
from warnings import warn
import numpy as np
from sklearn.model_selection import KFold

# From the Public API
from SparseSC.utils.penalty_utils import get_max_v_pen, w_pen_guestimate
from SparseSC.cross_validation import CV_score
from SparseSC.tensor import tensor
from SparseSC.weights import weights

# TODO: Cleanup task 1:
#  random_state = gradient_seed, in the calls to CV_score() and tensor() are
#  only used when grad splits is not None... need to better control this...


def fit(
    X,
    Y,
    treated_units=None,
    weight_penalty=None,  # Float
    covariate_penalties=None,  # Float or an array of floats
    # PARAMETERS USED TO CONSTRUCT DEFAULT GRID COVARIATE_PENALTIES
    grid=None,  # USER SUPPLIED GRID OF COVARIATE PENALTIES
    min_v_pen=1e-6,
    max_v_pen=1,
    grid_points=20,
    choice="min",
    cv_folds=10,
    gradient_folds=10,
    gradient_seed=10101,
    model_type="retrospective",
    custom_donor_pool=None,
    # VERBOSITY
    progress=True,
    **kwargs
):
    r"""

    :param X: Matrix of features
    :type X: matrix of floats

    :param Y: Matrix of targets
    :type Y: matrix of floats

    :param model_type:  Type of model being
        fit. One of ``"retrospective"``, ``"prospective"``,
        ``"prospective-restricted"`` or ``"full"``
    :type model_type: str, default = ``"retrospective"``

    :param treated_units:  An iterable indicating the rows
        of `X` and `Y` which contain data from treated units.
    :type treated_units: int[], Optional

    :param weight_penalty: Penalty applied to the difference
        between the current weights and the null weights (1/n). default
        provided by :func:``w_pen_guestimate``.
    :type weight_penalty: float, Optional

    :param covariate_penalties: penalty
        (penalties) applied to the magnitude of the covariate weights.
        Defaults to ``[ Lambda_c_max * g for g in grid]``, where
        `Lambda_c_max` is determined via :func:`get_max_v_pen` .
    :type covariate_penalties: float | float[], optional

    :param grid: only used when `covariate_penalties` is not provided.
        Defaults to ``np.exp(np.linspace(np.log(min_v_pen),np.log(max_v_pen),grid_points))``
    :type grid: float | float[], optional

    :param min_v_pen: Lower bound for ``grid`` when
        ``covariate_penalties`` and ``grid`` are not provided.  Must be in the
        range ``(0,1)``
    :type min_v_pen: float, default = 1e-6

    :param max_v_pen: Upper bound for ``grid`` when
        ``covariate_penalties`` and ``grid`` are not provided.  Must be in the
        range ``(0,1]``
    :type max_v_pen: float, default = 1

    :param grid_points: number of points in the ``grid`` parameter when
        ``covariate_penalties`` and ``grid`` are not provided
    :type grid_points: int, default = 20

    :param choice: Method for choosing from among the
        covariate_penalties.  Only used when covariate_penalties is an
        iterable.  Defaults to ``"min"`` which selects the v_pen parameter
        associated with the lowest cross validation error.
    :type choice: str or function. default = ``"min"``

    :param cv_folds: An integer number of Cross Validation folds passed to
        :func:`sklearn.model_selection.KFold`, or an explicit list of train
        validation folds. TODO: These folds are calculated with
        ``KFold(...,shuffle=False)``, but instead, it should be assigned a
        random state.
    :type cv_folds: int or (int[],int[])[], default = 10

    :param gradient_folds: (default = 10) An integer
        number of Gradient folds passed to
        :func:`sklearn.model_selection.KFold`, or an explicit list of train
        validation folds, to be used `model_type` is one either ``"foo"``
        ``"bar"``.
    :type gradient_folds: int or (int[],int[])[]

    :param gradient_seed:  passed to :func:`sklearn.model_selection.KFold`
        to allow for consistent gradient folds across calls when
        `model_type` is one either ``"foo"`` ``"bar"`` with and
        `gradient_folds` is an integer.
    :type gradient_seed: int, default = 10101

    :param progress: Controls the level of verbosity.  If `True`, the
        messages indication the progress are printed to the console (stdout).
    :type progress: boolean, default = ``True``

    :param kwargs: Additional arguments passed to the optimizer (i.e.
        ``method`` or `scipy.optimize.minimize`).  See below.

    :param custom_donor_pool: By default all control units are allowed to be donors
        for all units. There are cases where this is not desired and so the user
        can pass in a matrix specifying a unit-specific donor pool (NxC matrix
        of booleans).
        Common reasons for restricting the allowability:
        (a) When we would like to reduce interpolation bias by restricting the
        donor pool to those units similar along certain features.
        (b) If units are not completely independent (for example there may be
        contamination between neighboring units). This is a violation of the
        Single Unit Treatment Value Assumption (SUTVA).
        Note: These are not used in the fitting stage (of V and penalties) just
        in final unit weight determination.
    :type custom_donor_pool: boolean, default = ``None``

    :Keyword Args:

        * **method** (str or callable) -- The method or function
            responsible for performing gradient  descent in the covariate
            space.  If a string, it is passed as the ``method`` argument to
            :func:`scipy.optimize.minimize`.  Otherwise, ``method`` must be
            a function with a signature compatible with
            :func:`scipy.optimize.minimize`
            (``method(fun,x0,grad,**kwargs)``) which returns an object
            having ``x`` and ``fun`` attributes. (Default =
            :func:`SparseSC.optimizers.cd_line_search.cdl_search`)

        * **learning_rate** *(float, Default = 0.2)*  -- The initial learning rate
            which determines the initial step size, which is set to
            ``learning_rate * null_model_error / gradient``. Must be between 0 and
            1.

        * **learning_rate_adjustment** *(float, Default = 0.9)* -- Adjustment factor
            applied to the learning rate applied between iterations when the
            optimal step size returned by :func:`scipy.optimize.line_search` is
            greater less than 1, else the step size is adjusted by
            ``1/learning_rate_adjustment``. Must be between 0 and 1,

        * **tol** *(float, Default = 0.0001)* -- Tolerance used for the stopping
            rule based on the proportion of the in-sample residual error
            reduced in the last step of the gradient descent.

    :returns: A :class:`SparseSCFit` object containing details of the fitted model.
    :rtype: :class:`SparseSCFit`

    :raises ValueError: when ``treated_units`` is not None and not an
            ``iterable``, or when model_type is not one of the allowed values
    """

    assert X.shape[0] == Y.shape[0]
    verbose = kwargs.get("verbose",1)

    if treated_units is not None:

        # --------------------------------------------------
        # Phase 0: Data wrangling
        # --------------------------------------------------

        try:
            iter(treated_units)
        except TypeError:
            raise ValueError("treated_units must be an iterable")

        assert len(set(treated_units)) == len(
            treated_units
        ), (
            "duplicated values in treated_units are not allowed"
        )  # pylint: disable=line-too-long
        assert all(unit < Y.shape[0] for unit in treated_units)
        assert all(unit >= 0 for unit in treated_units)

        control_units = [u for u in range(Y.shape[0]) if u not in treated_units]

        Xtrain = X[control_units, :]
        Xtest = X[treated_units, :]
        Ytrain = Y[control_units, :]
        Ytest = Y[treated_units, :]

        # --------------------------------------------------
        # (sensible?) defaults
        # --------------------------------------------------
        # Get the weight penalty guestimate:  very quick ( milliseconds )
        if weight_penalty is None:
            weight_penalty = w_pen_guestimate(Xtrain)
        if covariate_penalties is None:
            if grid is None:
                grid = np.exp(
                    np.linspace(np.log(min_v_pen), np.log(max_v_pen), grid_points)
                )
            # GET THE MAXIMUM v_penS: quick ~ ( seconds to tens of seconds )
            v_pen_max = get_max_v_pen(
                Xtrain,
                Ytrain,
                w_pen=weight_penalty,
                grad_splits=gradient_folds,
                verbose=verbose,
            )
            covariate_penalties = grid * v_pen_max

        if model_type == "retrospective":
            # Retrospective Treatment Effects:  ( *model_type = "prospective"*)

            # --------------------------------------------------
            # Phase 1: extract cross fold residual errors for each v_pen
            # --------------------------------------------------

            # SCORES FOR EACH VALUE OF THE GRID: very slow ( minutes to hours )
            scores = CV_score(
                X=Xtrain,
                Y=Ytrain,
                splits=cv_folds,
                v_pen=covariate_penalties,
                progress=progress,
                w_pen=weight_penalty,
                grad_splits=gradient_folds,
                random_state=gradient_seed,  # TODO: Cleanup Task 1
                quiet=not progress,
                **kwargs
            )

            # GET THE INDEX OF THE BEST SCORE
            best_v_pen = __choose(scores, covariate_penalties, choice)

            # --------------------------------------------------
            # Phase 2: extract V and weights: slow ( tens of seconds to minutes )
            # --------------------------------------------------

            best_V = tensor(
                X=Xtrain,
                Y=Ytrain,
                v_pen=best_v_pen,
                grad_splits=gradient_folds,
                random_state=gradient_seed,  # TODO: Cleanup Task 1
                **kwargs
            )

        elif model_type == "prospective":
            # we're doing in-sample "predictions" -- i.e. we're directly optimizing the
            # observed || Y_ctrl - W Y_ctrl ||

            try:
                iter(gradient_folds)
            except TypeError:
                gradient_folds = KFold(
                    gradient_folds, shuffle=True, random_state=gradient_seed
                ).split(np.arange(X.shape[0]))
                gradient_folds = [
                    [
                        list(set(train).union(treated_units)),
                        list(set(test).difference(treated_units)),
                    ]
                    for train, test in gradient_folds
                ]  # pylint: disable=line-too-long
                gradient_folds = [
                    [train, test]
                    for train, test in gradient_folds
                    if len(train) != 0 and len(test) != 0
                ]  # pylint: disable=line-too-long
                gradient_folds.append([control_units, treated_units])
            else:
                # user supplied gradient folds
                gradient_folds = list(gradient_folds)
                treated_units_set = set(treated_units)

                # TODO: this condition logic is untested:
                if not any(treated_units_set == set(gf[1]) for gf in gradient_folds):
                    warn(
                        "User supplied gradient_folds will be re-formed for compatibility with model_type 'prospective'"
                    )  # pylint: disable=line-too-long
                    gradient_folds = [
                        [
                            list(set(train).union(treated_units)),
                            list(set(test).difference(treated_units)),
                        ]
                        for train, test in gradient_folds
                    ]  # pylint: disable=line-too-long
                    gradient_folds = [
                        [train, test]
                        for train, test in gradient_folds
                        if len(train) != 0 and len(test) != 0
                    ]  # pylint: disable=line-too-long
                    gradient_folds.append([control_units, treated_units])

            # --------------------------------------------------
            # Phase 1: extract cross fold residual errors for each v_pen
            # --------------------------------------------------

            # SCORES FOR EACH VALUE OF THE GRID: very slow ( minutes to hours )
            scores = CV_score(
                X=X,
                Y=Y,
                splits=cv_folds,
                v_pen=covariate_penalties,
                progress=progress,
                w_pen=weight_penalty,
                grad_splits=gradient_folds,
                random_state=gradient_seed,  # TODO: Cleanup Task 1
                quiet=not progress,
                **kwargs
            )

            # GET THE INDEX OF THE BEST SCORE
            best_v_pen = __choose(scores, covariate_penalties, choice)

            # --------------------------------------------------
            # Phase 2: extract V and weights: slow ( tens of seconds to minutes )
            # --------------------------------------------------

            best_V = tensor(
                X=X,
                Y=Y,
                v_pen=best_v_pen,
                grad_splits=gradient_folds,
                random_state=gradient_seed,  # TODO: Cleanup Task 1
                **kwargs
            )

        elif model_type == "prospective-restricted":
            # we're doing in-sample -- i.e. we're optimizing hold-out error in
            # the controls ( || Y_ctrl - W Y_ctrl || ) in the hopes that the
            # chosen penalty parameters and V matrix also optimizes the
            # unobserved ( || Y_treat - W Y_ctrl || ) in counter factual

            # --------------------------------------------------
            # Phase 1: extract cross fold residual errors for each v_pen
            # --------------------------------------------------

            # SCORES FOR EACH VALUE OF THE GRID: very slow ( minutes to hours )
            scores = CV_score(
                X=Xtrain,
                Y=Ytrain,
                X_treat=Xtest,
                Y_treat=Ytest,
                splits=cv_folds,
                v_pen=covariate_penalties,
                progress=progress,
                w_pen=weight_penalty,
                quiet=not progress,
                **kwargs
            )

            # GET THE INDEX OF THE BEST SCORE
            best_v_pen = __choose(scores, covariate_penalties, choice)

            # --------------------------------------------------
            # Phase 2: extract V and weights: slow ( tens of seconds to minutes )
            # --------------------------------------------------

            best_V = tensor(
                X=Xtrain,
                Y=Ytrain,
                X_treat=Xtest,
                Y_treat=Ytest,
                v_pen=best_v_pen,
                **kwargs
            )

        else:
            raise ValueError(
                "unexpected model_type '%s' or treated_units = None" % model_type
            )

        # GET THE BEST SET OF WEIGHTS
        sc_weights = np.empty((X.shape[0], Ytrain.shape[0]))
        if custom_donor_pool is None:
            custom_donor_pool_t = None
            custom_donor_pool_c = None
        else:
            custom_donor_pool_t = custom_donor_pool[treated_units, :]
            custom_donor_pool_c = custom_donor_pool[control_units, :]
        sc_weights[treated_units, :] = weights(
            Xtrain,
            Xtest,
            V=best_V,
            w_pen=weight_penalty,
            custom_donor_pool=custom_donor_pool_t,
        )
        sc_weights[control_units, :] = weights(
            Xtrain,
            V=best_V,
            w_pen=weight_penalty,
            custom_donor_pool=custom_donor_pool_c,
        )
    else:

        if model_type != "full":
            raise ValueError(
                "Unexpected model_type ='%s' or treated_units is not None" % model_type
            )  # pylint: disable=line-too-long

        control_units = None

        # --------------------------------------------------
        # (sensible?) defaults
        # --------------------------------------------------
        if covariate_penalties is None:
            if grid is None:
                grid = np.exp(
                    np.linspace(np.log(min_v_pen), np.log(max_v_pen), grid_points)
                )
            # GET THE MAXIMUM v_penS: quick ~ ( seconds to tens of seconds )
            v_pen_max = get_max_v_pen(
                X, Y, w_pen=weight_penalty, grad_splits=gradient_folds, verbose=verbose
            )
            covariate_penalties = grid * v_pen_max

        # Get the weight penalty guestimate:  very quick ( milliseconds )
        if weight_penalty is None:
            weight_penalty = w_pen_guestimate(X)

        # --------------------------------------------------
        # Phase 1: extract cross fold residual errors for each v_pen
        # --------------------------------------------------

        # SCORES FOR EACH VALUE OF THE GRID: very slow ( minutes to hours )
        scores = CV_score(
            X=X,
            Y=Y,
            splits=cv_folds,
            v_pen=covariate_penalties,
            progress=progress,
            w_pen=weight_penalty,
            grad_splits=gradient_folds,
            random_state=gradient_seed,  # TODO: Cleanup Task 1
            quiet=not progress,
            **kwargs
        )

        # GET THE INDEX OF THE BEST SCORE
        best_v_pen = __choose(scores, covariate_penalties, choice)

        # --------------------------------------------------
        # Phase 2: extract V and weights: slow ( tens of seconds to minutes )
        # --------------------------------------------------

        best_V = tensor(
            X=X,
            Y=Y,
            v_pen=best_v_pen,
            grad_splits=gradient_folds,
            random_state=gradient_seed,  # TODO: Cleanup Task 1
            **kwargs
        )

        # GET THE BEST SET OF WEIGHTS
        sc_weights = weights(
            X, V=best_V, w_pen=weight_penalty, custom_donor_pool=custom_donor_pool
        )

    return SparseSCFit(
        X,
        Y,
        control_units,
        treated_units,
        model_type,
        # fitting parameters
        best_v_pen,
        weight_penalty,
        covariate_penalties,
        best_V,
        # Fitted Synthetic Controls
        sc_weights,
    )


def __choose(scores, covariate_penalties, choice):
    """ helper function which implements the choice of covariate weights penalty parameter
    """
    # GET THE INDEX OF THE BEST SCORE
    try:
        iter(covariate_penalties)
    except TypeError:
        best_v_pen = scores
    else:
        if choice == "min":
            best_i = np.argmin(scores)
            best_v_pen = (covariate_penalties)[best_i]
        elif callable(choice):
            best_v_pen = choice(scores)
        else:
            # TODO: this is a terrible place to throw this error
            raise ValueError("Unexpected value for choice parameter: %s" % choice)

    return best_v_pen


class SparseSCFit(object):
    """ 
    A class representing the results of a Synthetic Control model instance.
    """

    def __init__(
        self,
        # Data
        X,
        Y,
        control_units,
        treated_units,
        model_type,
        # fitting parameters
        V_penalty,
        weight_penalty,
        covariate_penalties,
        V,
        # Fitted Synthetic Controls
        sc_weights,
    ):

        # DATA
        self.X = X
        self.Y = Y
        self.control_units = control_units
        self.treated_units = treated_units
        self.model_type = model_type

        # FITTING PARAMETERS
        self.V_penalty = V_penalty
        self.weight_penalty = weight_penalty
        self.covariate_penalties = covariate_penalties
        self.V = V

        # FITTED SYNTHETIC CONTROLS
        self.sc_weights = sc_weights

    def predict(self, Ydonor=None):
        """ predict method
        """
        if Ydonor is None:
            if self.model_type != "full":
                Ydonor = self.Y[self.control_units, :]
            else:
                Ydonor = self.Y
        return self.sc_weights.dot(Ydonor)

    def __str__(self):
        """ 
        Print details of the fit to the console
        """
        ret_str = (
            "Model type: "
            + self.model_type
            + "\n"
            + "V penalty: "
            + str(self.V_penalty)
            + "\n"
            + "W penalty: "
            + str(self.weight_penalty)
            + "\n"
            + "V:"
            + "\n"
            + str(np.diag(self.V))
        )

        # TODO: CALCULATE ERRORS AND R-SQUARED'S
        # ct_prediction_error = Y_SC_test - Ytest
        # null_model_error = Ytest - np.mean(Xtest)
        # betternull_model_error = (Ytest.T - np.mean(Xtest,1)).T
        # print("#--------------------------------------------------")
        # print("OUTER FOLD %s OF %s: Group Mean R-squared: %0.3f%%; Individual Mean R-squared: %0.3f%%" % (
        #        i + 1,
        #        100*(1 - np.power(ct_prediction_error,2).sum()  / np.power(null_model_error,2).sum()) ,
        #        100*(1 - np.power(ct_prediction_error,2).sum()  /np.power(betternull_model_error,2).sum() )))
        # print("#--------------------------------------------------")

        return ret_str

    def show(self):
        """ display goodness of figures illustrating goodness of fit
        """
        raise NotImplementedError()

