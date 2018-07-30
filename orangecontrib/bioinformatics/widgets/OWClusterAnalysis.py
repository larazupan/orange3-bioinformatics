""" OWClusterAnalysis """
import sys
import numpy as np

from AnyQt.QtWidgets import (
    QTableView, QHeaderView, QHBoxLayout,
    QSplitter
)
from AnyQt.QtCore import (
    Qt, QSize
)

from Orange.widgets.gui import (
    vBox, widgetBox, widgetLabel, spin, doubleSpin, comboBox
)
from Orange.widgets.widget import OWWidget, Msg
from Orange.widgets.settings import Setting, ContextSetting, DomainContextHandler
from Orange.widgets.utils.signals import Output, Input
from Orange.widgets import settings
from Orange.widgets.utils import itemmodels
from Orange.data import StringVariable, DiscreteVariable, Table, Domain

from orangecontrib.bioinformatics.widgets.utils.data import (
    TAX_ID, GENE_AS_ATTRIBUTE_NAME, GENE_ID_COLUMN, GENE_ID_ATTRIBUTE,
    ERROR_ON_MISSING_ANNOTATION, ERROR_ON_MISSING_GENE_ID, ERROR_ON_MISSING_TAX_ID
)
from orangecontrib.bioinformatics.widgets.utils.gui import HTMLDelegate, GeneSetsSelection, GeneScoringWidget
from orangecontrib.bioinformatics.cluster_analysis import Cluster, ClusterModel, GENE_SETS_COUNT, GENE_COUNT


class OWClusterAnalysis(OWWidget):
    name = "Cluster Analysis"
    description = "The widget displays differentially expressed genes that characterize the cluster, " \
                  "and corresponding gene terms that describe differentially expressed genes"
    icon = "../widgets/icons/OWClusterAnalysis.svg"
    priority = 100

    class Inputs:
        data_table = Input('Data', Table)
        custom_sets = Input('Custom Gene Sets', Table)

    class Outputs:
        selected_data = Output('Selected Data', Table)

    class Information(OWWidget.Information):
        pass

    class Warning(OWWidget.Warning):
        mannwhitneyu = Msg('{}, {}.')
        no_selected_gene_sets = Msg('No gene set selected, select them from Gene Sets box.')

    class Error(OWWidget.Error):
        missing_annotation = Msg(ERROR_ON_MISSING_ANNOTATION)
        missing_gene_id = Msg(ERROR_ON_MISSING_GENE_ID)
        missing_tax_id = Msg(ERROR_ON_MISSING_TAX_ID)
        organism_mismatch = Msg('Organism in input data and custom gene sets does not match')

    settingsHandler = DomainContextHandler()
    cluster_indicator = ContextSetting(None)
    stored_gene_sets_selection = ContextSetting(tuple())

    scoring_method_selection = ContextSetting(0)
    scoring_method_design = ContextSetting(0)

    # genes filter
    min_gene_count = Setting(20)
    use_gene_count_filter = Setting(True)

    max_gene_p_value = Setting(0.1)
    use_gene_pval_filter = Setting(False)

    max_gene_fdr = Setting(0.1)
    use_gene_fdr_filter = Setting(True)

    # gene sets filter
    min_gs_count = Setting(5)
    use_gs_count_filter = Setting(True)

    max_gs_p_value = Setting(0.1)
    use_gs_pval_filter = Setting(False)

    max_gs_fdr = Setting(0.1)
    use_gs_max_fdr = Setting(True)

    # auto commit results
    auto_commit = settings.Setting(False)

    custom_gene_set_indicator = settings.Setting(None)

    def __init__(self):
        super().__init__()

        # widget attributes
        self.input_data = None
        self.input_genes_names = []
        self.input_genes_ids = []

        self.tax_id = None
        self.use_attr_names = None
        self.gene_id_attribute = None

        # custom gene set input
        self.feature_model = itemmodels.DomainModel(valid_types=(DiscreteVariable, StringVariable))
        self.custom_data = None
        self.custom_tax_id = None
        self.custom_use_attr_names = None
        self.custom_gene_id_attribute = None
        self.custom_gene_id_column = None
        self.num_of_custom_sets = None

        self.rows_by_cluster = None
        self.clusters = []

        # data model
        self.cluster_info_model = None

        # Info
        info_box = vBox(self.controlArea, 'Info')
        self.input_info = widgetLabel(info_box)

        # Cluster selection
        self.cluster_indicator_model = itemmodels.DomainModel(valid_types=(DiscreteVariable,))
        box = widgetBox(self.controlArea, 'Cluster Indicator')
        self.cluster_indicator_combobox = comboBox(box, self, 'cluster_indicator',
                                                   model=self.cluster_indicator_model,
                                                   sendSelectedValue=True,
                                                   callback=self.invalidate)

        # Gene scoring
        box = widgetBox(self.controlArea, 'Gene Scoring')
        self.gene_scoring = GeneScoringWidget(box, self)
        self.gene_scoring.set_method_selection_area('scoring_method_selection')
        self.gene_scoring.set_method_design_area('scoring_method_design')

        # Gene Sets widget
        gene_sets_box = widgetBox(self.controlArea, "Gene Sets")
        self.gs_widget = GeneSetsSelection(gene_sets_box, self, 'stored_gene_sets_selection')
        self.gs_widget.hierarchy_tree_widget.itemClicked.connect(self.__gene_sets_enrichment)

        # custom gene sets area
        box = vBox(self.controlArea, "Custom Gene Sets")

        if self.custom_gene_set_indicator not in self.feature_model:
            self.custom_gene_set_indicator = None

        self.gs_label_combobox = comboBox(box, self, "custom_gene_set_indicator", sendSelectedValue=True,
                                          model=self.feature_model, callback=self.handle_custom_gene_sets)
        self.gs_label_combobox.setDisabled(True)

        # main area
        splitter = QSplitter(Qt.Horizontal, self.mainArea)
        self.mainArea.layout().addWidget(splitter)

        genes_filter = widgetBox(splitter, 'Filter Genes', orientation=QHBoxLayout())
        spin(genes_filter, self, 'min_gene_count', 0, GENE_COUNT,
             label='Count',
             tooltip='Minimum genes count',
             checked='use_gene_count_filter',
             callback=self.filter_genes,
             callbackOnReturn=True,
             checkCallback=self.filter_genes)

        doubleSpin(genes_filter, self, 'max_gene_p_value', 0.0, 1.0, 0.0001,
                   label='p-value',
                   tooltip='Maximum p-value of the enrichment score',
                   checked='use_gene_pval_filter',
                   callback=self.filter_genes,
                   callbackOnReturn=True,
                   checkCallback=self.filter_genes
                   )

        doubleSpin(genes_filter, self, 'max_gene_fdr', 0.0, 1.0, 0.0001,
                   label='FDR',
                   tooltip='Maximum false discovery rate',
                   checked='use_gene_fdr_filter',
                   callback=self.filter_genes,
                   callbackOnReturn=True,
                   checkCallback=self.filter_genes
                   )

        gene_sets_filter = widgetBox(splitter, 'Filter Gene Sets', orientation=QHBoxLayout())
        spin(gene_sets_filter, self, 'min_gs_count', 0, GENE_SETS_COUNT,
             label='Count',
             tooltip='Minimum genes count',
             checked='use_gs_count_filter',
             callback=self.filter_gene_sets,
             callbackOnReturn=True,
             checkCallback=self.filter_gene_sets)

        doubleSpin(gene_sets_filter, self, 'max_gs_p_value', 0.0, 1.0, 0.0001,
                   label='p-value',
                   tooltip='Maximum p-value of the enrichment score',
                   checked='use_gs_pval_filter',
                   callback=self.filter_gene_sets,
                   callbackOnReturn=True,
                   checkCallback=self.filter_gene_sets
                   )

        doubleSpin(gene_sets_filter, self, 'max_gs_fdr', 0.0, 1.0, 0.0001,
                   label='FDR',
                   tooltip='Maximum false discovery rate',
                   checked='use_gs_max_fdr',
                   callback=self.filter_gene_sets,
                   callbackOnReturn=True,
                   checkCallback=self.filter_gene_sets
                   )

        self.cluster_info_view = QTableView()
        self.cluster_info_view.verticalHeader().setVisible(False)
        self.cluster_info_view.setItemDelegate(HTMLDelegate())
        self.cluster_info_view.horizontalHeader().hide()
        self.cluster_info_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.mainArea.layout().addWidget(self.cluster_info_view)

    def sizeHint(self):
        return QSize(800, 600)

    def __update_info_box(self):
        info_string = ''
        if self.input_genes_ids:
            info_string += '{} samples, {} clusters\n'.format(
                self.input_data.X.shape[0], len(self.clusters) if self.clusters else '?')
            info_string += '{:,d} unique genes\n'.format(len(self.input_genes_ids))
        else:
            info_string += 'No genes on input.\n'

        if self.custom_data:
            info_string += '{} marker genes in {} sets\n'.format(self.custom_data.X.shape[0], self.num_of_custom_sets)

        self.input_info.setText(info_string)

    def __set_cluster_info_model(self):
        self.cluster_info_view.setModel(None)

        self.cluster_info_model = ClusterModel(self)
        self.cluster_info_model.add_rows(self.clusters)

        # add model to the view
        self.cluster_info_view.setModel(self.cluster_info_model)
        # call sizeHint function
        self.cluster_info_view.resizeRowsToContents()
        self.cluster_info_view.selectionModel().selectionChanged.connect(self.commit)

    def __set_clusters(self):
        self.clusters = []
        if self.cluster_indicator and self.input_data:
            self.rows_by_cluster = np.asarray(self.input_data.get_column_view(self.cluster_indicator)[0], dtype=int)

            for index, name in enumerate(self.cluster_indicator.values):
                cluster = Cluster(name, index)
                self.clusters.append(cluster)
                cluster.set_genes(self.input_genes_names, self.input_genes_ids)

    def __set_genes(self):
        self.input_genes_names = []
        self.input_genes_ids = []

        if self.use_attr_names:
            for variable in self.input_data.domain.attributes:
                self.input_genes_names.append(str(variable.name))
                self.input_genes_ids.append(str(variable.attributes.get(self.gene_id_attribute, np.nan)))

    def filter_genes(self):
        if self.cluster_info_model:
            for cluster in self.clusters:
                cluster.use_gene_count = self.use_gene_count_filter

            # filter genes
            # note: after gene filter is applied, we need to recalculate gene set enrichment
            self.cluster_info_model.apply_gene_filters(
                self.min_gene_count if self.use_gene_count_filter else GENE_COUNT,
                self.max_gene_p_value if self.use_gene_pval_filter else None,
                self.max_gene_fdr if self.use_gene_fdr_filter else None)

            # recalculate gene set enrichment
            self.__gene_sets_enrichment()
            # call sizeHint function
            self.cluster_info_view.resizeRowsToContents()

    def filter_gene_sets(self):
        if self.cluster_info_model:
            # filter gene sets
            self.cluster_info_model.apply_gene_sets_filters(
                self.min_gs_count if self.use_gs_count_filter else GENE_SETS_COUNT,
                self.max_gs_p_value if self.use_gs_pval_filter else None,
                self.max_gs_fdr if self.use_gs_max_fdr else None)

            # call sizeHint function
            self.cluster_info_view.resizeRowsToContents()

    def __gene_enrichment(self):
        # TODO: move this to the worker thread
        design = bool(self.gene_scoring.get_selected_desig())  # if true cluster vs. cluster else cluster vs rest
        method = self.gene_scoring.get_selected_method()

        try:
            self.cluster_info_model.score_genes(design, self.input_data.X, self.rows_by_cluster, method)
        except ValueError as e:
            self.Warning.mannwhitneyu(str(e), 'p-values are set to 1')

        self.filter_genes()

    def __gene_sets_enrichment(self):
        # TODO: move this to the worker thread
        self.Warning.no_selected_gene_sets.clear()
        selected_sets = self.gs_widget.get_hierarchies(only_selected=True)

        if len(selected_sets) == 0:
            self.Warning.no_selected_gene_sets()

        # save setting on selected hierarchies
        self.stored_gene_sets_selection = tuple(selected_sets)
        ref_genes = set(self.input_genes_ids)

        try:
            self.cluster_info_model.gene_sets_enrichment(self.gs_widget.gs_object,
                                                         selected_sets,
                                                         ref_genes)
        except Exception as e:
            # TODO: possible exceptions?

            raise e

        self.filter_gene_sets()

    def invalidate(self):
        self.Warning.clear()
        self.Error.clear()

        if self.input_data:
            self.__set_genes()
            self.__set_clusters()
            self.__set_cluster_info_model()

            # note: when calling self.__gene_enrichment we calculate gse automatically.
            #       No need to call self.__gene_sets_enrichment here
            self.__gene_enrichment()
            self.__update_info_box()

    @Inputs.data_table
    def handle_input(self, data):
        self.closeContext()
        self.input_data = None
        self.stored_gene_sets_selection = tuple()
        self.input_genes_names = []
        self.input_genes_ids = []
        self.tax_id = None
        self.use_attr_names = None
        self.gene_id_attribute = None
        self.cluster_indicator = None
        self.cluster_indicator_model.set_domain(None)

        if data:
            self.input_data = data
            self.cluster_indicator_model.set_domain(self.input_data.domain)

            self.tax_id = str(self.input_data.attributes.get(TAX_ID, None))
            self.use_attr_names = self.input_data.attributes.get(GENE_AS_ATTRIBUTE_NAME, None)
            self.gene_id_attribute = self.input_data.attributes.get(GENE_ID_ATTRIBUTE, None)

            if self.use_attr_names is not None and self.gene_id_attribute is None:

                if self.tax_id is None:
                    self.Error.missing_annotation()
                    return

                self.Error.missing_gene_id()
                return

            elif self.tax_id is None:
                self.Error.missing_tax_id()
                return

            self.openContext(self.input_data.domain)

            self.gs_widget.load_gene_sets(self.tax_id)
            if self.cluster_indicator_model:
                self.cluster_indicator = self.cluster_indicator_model[0]

            self.invalidate()

    @Inputs.custom_sets
    def handle_custom_input(self, data):
        self.closeContext()
        self.custom_data = None
        self.custom_tax_id = None
        self.custom_use_attr_names = None
        self.custom_gene_id_attribute = None
        self.custom_gene_id_column = None
        self.num_of_custom_sets = None
        self.feature_model.set_domain(None)

        if data:
            self.custom_data = data
            self.feature_model.set_domain(self.custom_data.domain)
            self.custom_tax_id = str(self.custom_data.attributes.get(TAX_ID, None))
            self.custom_use_attr_names = self.custom_data.attributes.get(GENE_AS_ATTRIBUTE_NAME, None)
            self.custom_gene_id_attribute = self.custom_data.attributes.get(GENE_ID_ATTRIBUTE, None)
            self.custom_gene_id_column = self.custom_data.attributes.get(GENE_ID_COLUMN, None)

            if not (self.custom_use_attr_names is not None
                    and ((self.custom_gene_id_attribute is None) ^ (self.custom_gene_id_column is None))):

                if self.custom_tax_id is None:
                    self.Error.missing_annotation()
                    return

                self.Error.missing_gene_id()
                return

            elif self.custom_tax_id is None:
                self.Error.missing_tax_id()
                return

            if self.__check_organism_mismatch():
                self.Error.organism_mismatch()
                return

        if self.input_data:
            self.openContext(self.input_data.domain)

    def handleNewSignals(self):
        self.Error.clear()
        self.gs_label_combobox.setDisabled(False)

        if self.input_data:
            self.refresh_custom_gene_sets()
            self.handle_custom_gene_sets()

    def __check_organism_mismatch(self):
        """ Check if organisms from different inputs match.

        :return: True if there is a mismatch
        """
        if self.tax_id is not None and self.custom_tax_id is not None:
            return self.tax_id != self.custom_tax_id
        return False

    def _handle_future_model(self):
        if self.custom_gene_set_indicator in self.feature_model:
            index = self.feature_model.indexOf(self.custom_gene_set_indicator)
            self.custom_gene_set_indicator = self.feature_model[index]
        else:
            if self.feature_model:
                self.custom_gene_set_indicator = self.feature_model[0]
            else:
                self.custom_gene_set_indicator = None

    def handle_custom_gene_sets(self):
        self._handle_future_model()

        if self.custom_gene_set_indicator:
            if self.custom_data is not None and self.custom_gene_id_column is not None:

                if self.__check_organism_mismatch():
                    self.gs_label_combobox.setDisabled(True)
                    self.Error.organism_mismatch()
                    return

                if isinstance(self.custom_gene_set_indicator, DiscreteVariable):
                    gene_sets_names = self.custom_gene_set_indicator.values
                else:
                    gene_sets_names, _ = self.custom_data.get_column_view(self.custom_gene_set_indicator)

                self.num_of_custom_sets = len(set(gene_sets_names))
                gene_names, _ = self.custom_data.get_column_view(self.custom_gene_id_column)
                hierarchy_title = (self.input_data.name if self.input_data.name else 'Custom sets', )
                self.gs_widget.add_custom_sets(gene_sets_names, gene_names, hierarchy_title=hierarchy_title)

        self.__gene_sets_enrichment()
        self.__update_info_box()

    def refresh_custom_gene_sets(self):
        self.gs_widget.clear_custom_sets()
        self.gs_widget.update_gs_hierarchy()

    def commit(self):
        selection_model = self.cluster_info_view.selectionModel()
        selected_rows = selection_model.selectedRows()
        selected_cluster_indexes = set()
        selected_cluster_genes = set()

        if not self.input_data or not selected_rows:
            self.Outputs.selected_data.send(None)
            return

        for sel_row in selected_rows:
            cluster = sel_row.data()
            selected_cluster_indexes.add(cluster.index)
            [selected_cluster_genes.add(gene.ncbi_id) for gene in cluster.filtered_genes]

        # get columns of selected clusters
        selected_columns = [column for column in self.input_data.domain.attributes
                            if self.gene_id_attribute in column.attributes and
                            str(column.attributes[self.gene_id_attribute]) in selected_cluster_genes]

        domain = Domain(selected_columns, self.input_data.domain.class_vars, self.input_data.domain.metas)
        output_data = self.input_data.from_table(domain, self.input_data)

        # get rows of selected clusters
        selected_rows = [row_index for row_index, col_index in enumerate(self.rows_by_cluster)
                         if col_index in selected_cluster_indexes]

        # send to output signal
        self.Outputs.selected_data.send(output_data[selected_rows])


if __name__ == "__main__":

    def main(argv=None):
        from AnyQt.QtWidgets import QApplication
        app = QApplication(list(argv) if argv else [])

        w = OWClusterAnalysis()
        data = Table('https://datasets.orange.biolab.si/sc/aml-1k.pickle')
        w.show()
        w.handle_input(data)
        # w.cluster_indicator.initialize(data)
        rval = app.exec_()

        return rval

    sys.exit(main(sys.argv))
