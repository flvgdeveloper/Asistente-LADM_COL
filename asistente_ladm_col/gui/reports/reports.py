# -*- coding: utf-8 -*-
"""
/***************************************************************************
                              Asistente LADM_COL
                             --------------------
        begin                : 2018-10-17
        git sha              : :%H$
        copyright            : (C) 2018 by Germán Carrillo (BSF Swissphoto)
        email                : gcarrillo@linuxmail.org
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License v3.0 as          *
 *   published by the Free Software Foundation.                            *
 *                                                                         *
 ***************************************************************************/
"""
import functools
import json
import locale
import os
import stat
import tempfile
import time
import zipfile

from qgis.PyQt.QtCore import (Qt,
                              QObject,
                              QCoreApplication,
                              QSettings,
                              QUrl,
                              QFile,
                              QProcess,
                              QEventLoop,
                              QIODevice,
                              pyqtSignal)
from qgis.PyQt.QtWidgets import (QFileDialog,
                                 QProgressBar)
from qgis.core import (QgsWkbTypes,
                       QgsDataSourceUri,
                       QgsNetworkContentFetcherTask,
                       QgsApplication)

from asistente_ladm_col.config.general_config import (ANNEX_17_REPORT,
                                                      DEPENDENCIES_BASE_PATH,
                                                      DEPENDENCY_REPORTS_DIR_NAME,
                                                      TEST_SERVER,
                                                      REPORTS_REQUIRED_VERSION,
                                                      URL_REPORTS_LIBRARIES)
from asistente_ladm_col.lib.logger import Logger
from asistente_ladm_col.utils.qt_utils import normalize_local_url
from asistente_ladm_col.utils.java_utils import JavaUtils


class ReportGenerator(QObject):
    LOG_TAB = 'LADM-COL Reports'

    enable_action_requested = pyqtSignal(str, bool)

    def __init__(self, qgis_utils, ladm_data):
        QObject.__init__(self)
        self.qgis_utils = qgis_utils
        self.ladm_data = ladm_data
        self.logger = Logger()
        self.java_utils = JavaUtils()
        self.java_utils.download_java_completed.connect(self.download_java_complete)

        self.encoding = locale.getlocale()[1]
        # This might be unset
        if not self.encoding:
            self.encoding = 'UTF8'

        self._downloading = False

    def stderr_ready(self, proc):
        text = bytes(proc.readAllStandardError()).decode(self.encoding)
        self.logger.critical(__name__, text, tab=self.LOG_TAB)

    def stdout_ready(self, proc):
        text = bytes(proc.readAllStandardOutput()).decode(self.encoding)
        self.logger.info(__name__, text, tab=self.LOG_TAB)

    def update_yaml_config(self, db, config_path):
        text = ''
        qgs_uri = QgsDataSourceUri(db.uri)

        with open(os.path.join(config_path, 'config_template.yaml')) as f:
            text = f.read()
            text = text.format(
                '{}',
                DB_USER = qgs_uri.username(),
                DB_PASSWORD = qgs_uri.password(),
                DB_HOST = qgs_uri.host(),
                DB_PORT = qgs_uri.port(),
                DB_NAME = qgs_uri.database()
            )
        new_file_path = os.path.join(config_path, self.get_tmp_filename('yaml_config', 'yaml'))

        with open(new_file_path, 'w') as new_yaml:
            new_yaml.write(text)

        return new_file_path

    def get_layer_geojson(self, db, layer_name, plot_id, report_type):
        if report_type == ANNEX_17_REPORT:
            if layer_name == 'terreno':
                return db.get_annex17_plot_data(plot_id, 'only_id')
            elif layer_name == 'terrenos':
                return db.get_annex17_plot_data(plot_id, 'all_but_id')
            elif layer_name == 'terrenos_all':
                return db.get_annex17_plot_data(plot_id, 'all')
            elif layer_name == 'construcciones':
                return db.get_annex17_building_data()
            else:
                return db.get_annex17_point_data(plot_id)
        else: #report_type == ANT_MAP_REPORT:
            if layer_name == 'terreno':
                return db.get_ant_map_plot_data(plot_id, 'only_id')
            elif layer_name == 'terrenos':
                return db.get_ant_map_plot_data(plot_id, 'all_but_id')
            elif layer_name == 'terrenos_all':
                return db.get_annex17_plot_data(plot_id, 'all')
            elif layer_name == 'construcciones':
                return db.get_annex17_building_data()
            elif layer_name == 'puntoLindero':
                return db.get_annex17_point_data(plot_id)
            else: #layer_name == 'cambio_colindancia':
                return db.get_ant_map_neighbouring_change_data(plot_id)

    def update_json_data(self, db, json_spec_file, plot_id, tmp_dir, report_type):
        json_data = dict()
        with open(json_spec_file) as f:
            json_data = json.load(f)

        json_data['attributes']['id'] = plot_id
        json_data['attributes']['datasetName'] = db.schema
        layers = json_data['attributes']['map']['layers']
        for layer in layers:
            layer['geoJson'] = self.get_layer_geojson(db, layer['name'], plot_id, report_type)

        overview_layers = json_data['attributes']['overviewMap']['layers']
        for layer in overview_layers:
            layer['geoJson'] = self.get_layer_geojson(db, layer['name'], plot_id, report_type)

        new_json_file_path = os.path.join(tmp_dir, self.get_tmp_filename('json_data_{}'.format(plot_id), 'json'))
        with open(new_json_file_path, 'w') as new_json:
            new_json.write(json.dumps(json_data))

        return new_json_file_path

    def get_tmp_dir(self, create_random=True):
        if create_random:
            return tempfile.mkdtemp()

        return tempfile.gettempdir()

    def get_tmp_filename(self, basename, extension='gpkg'):
        return "{}_{}.{}".format(basename, str(time.time()).replace(".",""), extension)

    def generate_report(self, db, report_type):
        # Check if mapfish and Jasper are installed, otherwise show where to
        # download them from and return
        base_path = os.path.join(DEPENDENCIES_BASE_PATH, DEPENDENCY_REPORTS_DIR_NAME)
        bin_path = os.path.join(base_path, 'bin')
        if not os.path.exists(bin_path):
            self.logger.message_with_button_download_report_dependency_emitted.emit(
                QCoreApplication.translate("ReportGenerator",
                   "The dependency library to generate reports is not installed. Click on the button to download and install it."))
            return

        # Check version
        required_version_found = True
        version_path = os.path.join(base_path, 'version')
        if not os.path.exists(version_path):
            required_version_found = False
        else:
            version_found = ''
            with open(version_path) as f:
                version_found = f.read()
            if version_found.strip() != REPORTS_REQUIRED_VERSION:
                required_version_found = False

        if not required_version_found:
            self.logger.message_with_button_remove_report_dependency_emitted.emit(
                QCoreApplication.translate("ReportGenerator",
                    "The dependency library to generate reports was found, but does not match with the version required. Click the button to remove the installed version and try again."))
            return

        java_home_set = self.java_utils.set_java_home()
        if not java_home_set:
            self.java_utils.get_java_on_demand()
            self.logger.info_msg(__name__, QCoreApplication.translate("ReportGenerator",
                                                                         "Java is a prerequisite. Since it was not found, it is being configured..."))
            return

        plot_layer = self.qgis_utils.get_layer(db, db.names.OP_PLOT_T, QgsWkbTypes.PolygonGeometry, load=True)
        if not plot_layer:
            return

        selected_plots = plot_layer.selectedFeatures()
        if not selected_plots:
            self.logger.warning_msg(__name__, QCoreApplication.translate("ReportGenerator",
                                    "To generate reports, first select at least a plot!"))
            return

        # Where to store the reports?
        previous_folder = QSettings().value("Asistente-LADM_COL/reports/save_into_dir", ".")
        save_into_folder = QFileDialog.getExistingDirectory(
                        None,
                        QCoreApplication.translate("ReportGenerator", "Select a folder to save the reports to be generated"),
                        previous_folder)
        if not save_into_folder:
            self.logger.warning_msg(__name__, QCoreApplication.translate("ReportGenerator",
                                    "You need to select a folder where to save the reports before continuing."))
            return
        QSettings().setValue("Asistente-LADM_COL/reports/save_into_dir", save_into_folder)

        config_path = os.path.join(base_path, report_type)

        json_spec_file = os.path.join(config_path, 'spec_json_file.json')

        script_name = ''
        if os.name == 'posix':
            script_name = 'print'
        elif os.name == 'nt':
            script_name = 'print.bat'

        script_path = os.path.join(bin_path, script_name)
        if not os.path.isfile(script_path):
            self.logger.warning(__name__, "Script file for reports wasn't found! {}".format(script_path))
            return

        self.enable_action_requested.emit(report_type, False)

        # Update config file
        yaml_config_path = self.update_yaml_config(db, config_path)
        self.logger.debug(__name__, "Config file for reports: {}".format(yaml_config_path))

        total = len(selected_plots)
        step = 0
        count = 0
        tmp_dir = self.get_tmp_dir()

        # Progress bar setup
        progress = QProgressBar()
        if total == 1:
            progress.setRange(0, 0)
        else:
            progress.setRange(0, 100)
        progress.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.qgis_utils.create_progress_message_bar_emitted.emit(
            QCoreApplication.translate("ReportGenerator", "Generating {} report{}...").format(total, '' if total == 1 else 's'),
            progress)

        polygons_with_holes = []
        multi_polygons = []

        for selected_plot in selected_plots:
            plot_id = selected_plot[db.names.T_ID_F]

            geometry = selected_plot.geometry()
            abstract_geometry = geometry.get()
            if abstract_geometry.ringCount() > 1:
                polygons_with_holes.append(str(plot_id))
                self.logger.warning(__name__, QCoreApplication.translate("ReportGenerator",
                    "Skipping Annex 17 for plot with {}={} because it has holes. The reporter module does not support such polygons.").format(db.names.T_ID_F, plot_id))
                continue
            if abstract_geometry.numGeometries() > 1:
                multi_polygons.append(str(plot_id))
                self.logger.warning(__name__, QCoreApplication.translate("ReportGenerator",
                    "Skipping Annex 17 for plot with {}={} because it is a multi-polygon. The reporter module does not support such polygons.").format(db.names.T_ID_F, plot_id))
                continue

            # Generate data file
            json_file = self.update_json_data(db, json_spec_file, plot_id, tmp_dir, report_type)
            self.logger.debug(__name__, "JSON file for reports: {}".format(json_file))

            # Run sh/bat passing config and data files
            proc = QProcess()
            proc.readyReadStandardError.connect(
                functools.partial(self.stderr_ready, proc=proc))
            proc.readyReadStandardOutput.connect(
                functools.partial(self.stdout_ready, proc=proc))

            parcel_number = self.ladm_data.get_parcels_related_to_plots(db, [plot_id], db.names.OP_PARCEL_T_PARCEL_NUMBER_F) or ['']
            file_name = '{}_{}_{}.pdf'.format(report_type, plot_id, parcel_number[0])

            current_report_path = os.path.join(save_into_folder, file_name)
            proc.start(script_path, ['-config', yaml_config_path, '-spec', json_file, '-output', current_report_path])

            if not proc.waitForStarted():
                # Grant execution permissions
                os.chmod(script_path, stat.S_IXOTH | stat.S_IXGRP | stat.S_IXUSR | stat.S_IRUSR | stat.S_IRGRP)
                proc.start(script_path, ['-config', yaml_config_path, '-spec', json_file, '-output', current_report_path])

            if not proc.waitForStarted():
                proc = None
                self.logger.warning(__name__, "Couldn't execute script to generate report...")
            else:
                loop = QEventLoop()
                proc.finished.connect(loop.exit)
                loop.exec()

                self.logger.debug(__name__, "{}:{}".format(plot_id, proc.exitCode()))
                if proc.exitCode() == 0:
                    count += 1

                step += 1
                progress.setValue(step * 100 / total)

        os.remove(yaml_config_path)

        self.enable_action_requested.emit(report_type, True)
        self.logger.clear_message_bar()

        if total == count:
            if total == 1:
                msg = QCoreApplication.translate("ReportGenerator", "The report <a href='file:///{}'>{}</a> was successfully generated!").format(normalize_local_url(save_into_folder), file_name)
            else:
                msg = QCoreApplication.translate("ReportGenerator", "All reports were successfully generated in folder <a href='file:///{path}'>{path}</a>!").format(path=normalize_local_url(save_into_folder))

            self.logger.success_msg(__name__, msg)
        else:
            details_msg = ''
            if polygons_with_holes:
                details_msg += QCoreApplication.translate("ReportGenerator",
                                                          " The following polygons were skipped because they have holes and are not supported: {}.").format(
                    ", ".join(polygons_with_holes))
            if multi_polygons:
                details_msg += QCoreApplication.translate("ReportGenerator",
                                                          " The following polygons were skipped because they are multi-polygons and are not supported: {}.").format(
                    ", ".join(multi_polygons))

            if total == 1:
                msg = QCoreApplication.translate("ReportGenerator", "The report for plot {} couldn't be generated!{} See QGIS log (tab '{}') for details.").format(plot_id, details_msg, self.LOG_TAB)
            else:
                if count == 0:
                    msg = QCoreApplication.translate("ReportGenerator", "No report could be generated!{} See QGIS log (tab '{}') for details.").format(details_msg, self.LOG_TAB)
                else:
                    msg = QCoreApplication.translate("ReportGenerator", "At least one report couldn't be generated!{details_msg} See QGIS log (tab '{log_tab}') for details. Go to <a href='file:///{path}'>{path}</a> to see the reports that were generated.").format(details_msg=details_msg, path=normalize_local_url(save_into_folder), log_tab=self.LOG_TAB)

            self.logger.warning_msg(__name__, msg)

    def download_java_complete(self):
        self.logger.info_msg(__name__, QCoreApplication.translate("ReportGenerator",
                                                                  "Java was successfully configured!"), 5)

    def save_dependency_file(self, fetcher_task):
        if fetcher_task.reply() is not None:
            # Write response to tmp file
            tmp_file = tempfile.mktemp()
            out_file = QFile(tmp_file)
            out_file.open(QIODevice.WriteOnly)
            out_file.write(fetcher_task.reply().readAll())
            out_file.close()

            if not os.path.exists(DEPENDENCIES_BASE_PATH):
                os.makedirs(DEPENDENCIES_BASE_PATH)

            try:
                with zipfile.ZipFile(tmp_file, "r") as zip_ref:
                    zip_ref.extractall(DEPENDENCIES_BASE_PATH)
            except zipfile.BadZipFile as e:
                self.logger.warning_msg(__name__, QCoreApplication.translate("ReportGenerator",
                    "There was an error with the download. The downloaded file is invalid."))
            except PermissionError as e:
                self.logger.warning_msg(__name__, QCoreApplication.translate("ReportGenerator",
                    "Dependencies to generate reports couldn't be installed. Check if it is possible to write into this folder: <a href='file:///{path}'>{path}</a>").format(path=normalize_local_url(os.path.join(DEPENDENCIES_BASE_PATH), DEPENDENCY_REPORTS_DIR_NAME)))
            else:
                self.logger.info_msg(__name__, QCoreApplication.translate("ReportGenerator", "The dependency to generate reports is properly installed! Select plots and click again the button in the toolbar to generate reports."))

            try:
                os.remove(tmp_file)
            except:
                pass

        self._downloading = False

    def download_report_dependency(self):
        self.logger.clear_message_bar()
        if not self._downloading: # Already downloading report dependency?
            if self.qgis_utils.is_connected(TEST_SERVER):
                self._downloading = True
                fetcher_task = QgsNetworkContentFetcherTask(QUrl(URL_REPORTS_LIBRARIES))
                fetcher_task.fetched.connect(functools.partial(self.save_dependency_file, fetcher_task))
                QgsApplication.taskManager().addTask(fetcher_task)
            else:
                self.logger.warning_msg(__name__, QCoreApplication.translate("ReportGenerator",
                                        "There was a problem connecting to Internet."))
                self._downloading = False

