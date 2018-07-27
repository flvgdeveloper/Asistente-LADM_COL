import nose2

from qgis.core import QgsVectorLayer, QgsApplication
from asistente_ladm_col.tests.utils import get_test_copy_path
from qgis.testing import unittest
from asistente_ladm_col.gui.controlled_measurement_dialog import ControlledMeasurementDialog

from processing.core.Processing import Processing
from qgis.analysis import QgsNativeAlgorithms


class TestExport(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        Processing.initialize()
        QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())

    def test_get_point_groups(self):
        print('\nINFO: Validating controlled measurement (Point Grouping)...')
        gpkg_path = get_test_copy_path('geopackage/tests_data.gpkg')
        uri = gpkg_path + '|layername={layername}'.format(layername='tests_controlled_measurement')
        measure_layer = QgsVectorLayer(uri, 'tests_controlled_measurement', 'ogr')

        self.assertEqual(measure_layer.featureCount(), 38)

        bdef = [i.id() for i in measure_layer.getFeatures("\"def_type\"='Bien_Definido'")]
        self.assertEqual(len(bdef), 20)

        nbdef = [i.id() for i in measure_layer.getFeatures("\"def_type\"='No_Bien_Definido'")]
        self.assertEqual(len(nbdef), 18)

        # Test model with distance of 0.5 meters
        res, msg = ControlledMeasurementDialog.run_group_points_model(
            ControlledMeasurementDialog, measure_layer, 0.5, 'def_type')

        self.assertIsNotNone(res) # Model not found

        res_layer = res['native:mergevectorlayers_1:output']
        self.assertEqual(res_layer.featureCount(), 38)
        for g in range(1,10):
            dt = [f.id() for f in res_layer.getFeatures("\"belongs_to_group\"={}".format(g))]
            self.assertEqual(len(dt), 2) # All groups have 2 features

        features = res_layer.getFeatures("\"belongs_to_group\"=4")
        features = [f for f in features]
        self.assertEqual(sorted([f.attributes()[0] for f in features]) , [191, 192])

        # Test model with distance of 5 meters
        res, msg = ControlledMeasurementDialog.run_group_points_model(
            ControlledMeasurementDialog, measure_layer, 5.0, 'def_type')

        self.assertIsNotNone(res) # Model not found

        res_layer = res['native:mergevectorlayers_1:output']
        self.assertEqual(res_layer.featureCount(), 38)
        for g in [3, 8]:
            dt = [f.id() for f in res_layer.getFeatures("\"belongs_to_group\"={}".format(g))]
            self.assertEqual(len(dt), 4)
        features = res_layer.getFeatures("\"belongs_to_group\"=3")
        features = [f for f in features]
        self.assertEqual(sorted([f.attributes()[0] for f in features]), [189, 190, 191, 192])

    def test_time_groups_validation(self):
        print('\nINFO: Validating controlled measurement (Time Group Validation)...')
        gpkg_path = get_test_copy_path('geopackage/tests_data.gpkg')
        uri = gpkg_path + '|layername={layername}'.format(layername='tests_controlled_measurement')
        measure_layer = QgsVectorLayer(uri, 'tests_controlled_measurement', 'ogr')

        self.assertEqual(measure_layer.featureCount(), 38)

        bdef = [i.id() for i in measure_layer.getFeatures("\"def_type\"='Bien_Definido'")]
        self.assertEqual(len(bdef), 20)

        nbdef = [i.id() for i in measure_layer.getFeatures("\"def_type\"='No_Bien_Definido'")]
        self.assertEqual(len(nbdef), 18)

        # Test to check time validation with 0.5 meters of distance
        res, msg = ControlledMeasurementDialog.run_group_points_model(
            ControlledMeasurementDialog, measure_layer, 0.5, 'def_type')
        res_layer = res['native:mergevectorlayers_1:output']

        for i in [10, 6, 3]:
            features, no_features = ControlledMeasurementDialog.time_filter(ControlledMeasurementDialog,
                res_layer,
                res_layer.getFeatures("\"belongs_to_group\"={}".format(i)),
                idx=res_layer.fields().indexFromName("timestamp"),
                time_tolerance=30) # minutes

            feat = {10: 1, 6: 1, 3: 2}
            no_feat = {10: 1, 6: 1, 3: 0}
            self.assertEqual(len([feat for feat in features]), feat[i])
            self.assertEqual(len([feat for feat in no_features]), no_feat[i])


        # Test to check time validation with 5 meters of distance.
        res, msg = ControlledMeasurementDialog.run_group_points_model(
            ControlledMeasurementDialog, measure_layer, 5, 'def_type')

        res_layer = res['native:mergevectorlayers_1:output']

        for i in [8, 4, 3]:
            features, no_features = ControlledMeasurementDialog.time_filter(ControlledMeasurementDialog,
                res_layer,
                res_layer.getFeatures("\"belongs_to_group\"={}".format(i)),
                idx=res_layer.fields().indexFromName("timestamp"),
                time_tolerance=30)

            feat = {8: 2, 4: 1, 3: 4}
            no_feat = {8: 2, 4: 1, 3: 0}
            self.assertEqual(len([feat for feat in features]), feat[i])
            self.assertEqual(len([feat for feat in no_features]), no_feat[i])



if __name__ == '__main__':
    nose2.main()
