# coding=utf-8
from __future__ import absolute_import

import threading

import octoprint.plugin
from octoprint.events import Events

import datetime
import math
import flask
import os
import shutil
import tempfile

from octoprint_PrintJobHistory.common import CSVExportImporter
from octoprint_PrintJobHistory.models.FilamentModel import FilamentModel
from octoprint_PrintJobHistory.models.PrintJobModel import PrintJobModel
from octoprint_PrintJobHistory.models.TemperatureModel import TemperatureModel
from peewee import DoesNotExist

from .common.SettingsKeys import SettingsKeys
from .api.PrintJobHistoryAPI import PrintJobHistoryAPI
from .api import TransformPrintJob2JSON
from .DatabaseManager import DatabaseManager
from .CameraManager import CameraManager


class PrintJobHistoryPlugin(
							PrintJobHistoryAPI,
							octoprint.plugin.SettingsPlugin,
                            octoprint.plugin.AssetPlugin,
                            octoprint.plugin.TemplatePlugin,
							octoprint.plugin.StartupPlugin,
							octoprint.plugin.EventHandlerPlugin,
							#octoprint.plugin.SimpleApiPlugin
							):


	def initialize(self):
		self._preHeatPluginImplementation = None
		self._preHeatPluginImplementationState = None
		self._filamentManagerPluginImplementation = None
		self._filamentManagerPluginImplementationState = None
		self._displayLayerProgressPluginImplementation = None
		self._displayLayerProgressPluginImplementationState = None
		self._ultimakerFormatPluginImplementation = None
		self._ultimakerFormatPluginImplementationState = None



		pluginDataBaseFolder = self.get_plugin_data_folder()

		self._logger.info("Start initializing")
		# DATABASE
		self._databaseManager = DatabaseManager(self._logger)

		self._databaseManager.initDatabase(pluginDataBaseFolder, self._sendErrorMessageToClient)
		# databasePath = os.path.join(pluginDataBaseFolder, "printJobHistory.db")
		# self._databaseManager.initDatabase(databasePath)

		# CAMERA
		self._cameraManager = CameraManager(self._logger)
		pluginBaseFolder = self._basefolder

		self._cameraManager.initCamera(pluginDataBaseFolder, pluginBaseFolder, self._settings)

		self._settings.set( [SettingsKeys.SETTINGS_KEY_DATABASE_PATH], self._databaseManager.getDatabaseFileLocation())
		self._settings.set( [SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH], self._cameraManager.getSnapshotFileLocation())
		self._settings.save()

		# OTHER STUFF
		self._currentPrintJobModel = None

		self.alreadyCanceled = False

		self._logger.info("Done initializing")
	################################################################################################## private functions
	def _sendDataToClient(self, payloadDict):
		self._plugin_manager.send_plugin_message(self._identifier,
												 payloadDict)

	def _sendCSVUploadResultToClient(self, message, errorCollection):
		self._sendDataToClient(dict(action="csvImportResult",
									errorCollection=errorCollection,
									message=message))

	def _sendErrorMessageToClient(self, title, message):
		self._sendDataToClient(dict(action="errorPopUp",
									title= title,
									message=message))

	def _checkForMissingPluginInfos(self, sendToClient=False):

		self._displayLayerProgressPluginImplementationState = "enabled"
		self._preHeatPluginImplementationState = "enabled"
		self._filamentManagerPluginImplementationState = "enabled"
		self._ultimakerFormatPluginImplementationState = "enabled"

		if "preheat" in self._plugin_manager.plugins:
			plugin = self._plugin_manager.plugins["preheat"]
			if plugin != None and plugin.enabled == True:
				self._preHeatPluginImplementation = plugin.implementation
			else:
				self._preHeatPluginImplementationState = "disabled"
		else:
			self._preHeatPluginImplementationState = "missing"

		if "filamentmanager" in self._plugin_manager.plugins:
			plugin = self._plugin_manager.plugins["filamentmanager"]
			if plugin != None and plugin.enabled == True:
				self._filamentManagerPluginImplementation = plugin.implementation
			else:
				self._filamentManagerPluginImplementationState = "disabled"
		else:
			self._filamentManagerPluginImplementationState = "missing"

		if "DisplayLayerProgress" in self._plugin_manager.plugins:
			plugin = self._plugin_manager.plugins["DisplayLayerProgress"]
			if plugin != None and plugin.enabled == True:
				self._displayLayerProgressPluginImplementation = plugin.implementation
			else:
				self._displayLayerProgressPluginImplementationState = "disabled"
		else:
			self._displayLayerProgressPluginImplementationState = "missing"

		if "UltimakerFormatPackage" in self._plugin_manager.plugins:
			plugin = self._plugin_manager.plugins["UltimakerFormatPackage"]
			if plugin != None and plugin.enabled == True:
				self._ultimakerFormatPluginImplementation = plugin.implementation
			else:
				self._ultimakerFormatPluginImplementationState = "disabled"
		else:
			self._ultimakerFormatPluginImplementationState = "missing"

		self._logger.info("Plugin-State: "
						  "PreHeat=" + self._preHeatPluginImplementationState + " "
						  "DisplayLayerProgress=" + self._displayLayerProgressPluginImplementationState + " "
						  "filamentmanager=" + self._filamentManagerPluginImplementationState + " "
						  "ultimakerformat=" + self._ultimakerFormatPluginImplementationState)

		if sendToClient == True:
			missingMessage = ""

			if self._preHeatPluginImplementation == None:
				missingMessage = missingMessage + "<li>PreHeat (<b>" + self._preHeatPluginImplementationState + "</b>)</li>"

			if self._filamentManagerPluginImplementation == None:
				missingMessage = missingMessage + "<li>FilamentManager (<b>" + self._filamentManagerPluginImplementationState + "</b>)</li>"

			if self._displayLayerProgressPluginImplementation == None:
				missingMessage = missingMessage + "<li>DisplayLayerProgress (<b>" + self._displayLayerProgressPluginImplementationState + "</b>)</li>"

			if self._ultimakerFormatPluginImplementation == None:
				missingMessage = missingMessage + "<li>UltimakerFormatPackage (<b>" + self._ultimakerFormatPluginImplementationState + "</b>)</li>"

			if missingMessage != "":
				missingMessage = "<ul>" + missingMessage + "</ul>"
				self._sendDataToClient(dict(action="missingPlugin",
											message=missingMessage))


	# Grabs all informations for the filament attributes
	def _createAndAssignFilamentModel(self, printJob, payload):
		filemanentModel  = FilamentModel()

		fileData = self._file_manager.get_metadata(payload["origin"], payload["path"])

		filamentLength = None
		if "analysis" in fileData:
			if "filament" in fileData["analysis"]:
				if "tool0" in fileData["analysis"]["filament"]:
					filamentLength = fileData["analysis"]["filament"]["tool0"]['length']

		filemanentModel.calculatedLength = filamentLength

		if self._filamentManagerPluginImplementation != None:

			filemanentModel.usedLength = self._filamentManagerPluginImplementation.filamentOdometer.totalExtrusion[0]
			selectedSpool = self._filamentManagerPluginImplementation.filamentManager.get_all_selections(self._filamentManagerPluginImplementation.client_id)
			if  selectedSpool != None and len(selectedSpool) > 0:
				spoolData = selectedSpool[0]["spool"]
				spoolName = spoolData["name"]
				spoolCost = spoolData["cost"]
				spoolCostUnit = self._filamentManagerPluginImplementation._settings.get(["currencySymbol"])
				spoolWeight = spoolData["weight"]

				profileData = selectedSpool[0]["spool"]["profile"]
				diameter = profileData["diameter"]
				material = profileData["material"]
				vendor = profileData["vendor"]
				density = profileData["density"]

				filemanentModel.spoolName = spoolName
				filemanentModel.spoolCost = spoolCost
				filemanentModel.spoolCostUnit = spoolCostUnit
				filemanentModel.spoolWeight = spoolWeight

				filemanentModel.profileVendor = vendor
				filemanentModel.diameter = diameter
				filemanentModel.density = density
				filemanentModel.material = material

				radius = diameter / 2;
				volume = filemanentModel.usedLength * math.pi * radius * radius / 1000;
				usedWeight = volume * density

				filemanentModel.usedWeight = usedWeight
				filemanentModel.usedCost = spoolCost / spoolWeight * usedWeight

		printJob.addFilamentModel(filemanentModel)
		pass

	def _updatePrintJobModelWithLayerHeightInfos(self, payload):
		totalLayers = payload["totalLayer"]
		currentLayer = payload["currentLayer"]
		self._currentPrintJobModel.printedLayers = currentLayer + " / " + totalLayers

		totalHeightWithExtrusion = payload["totalHeightWithExtrusion"]
		currentHeight = payload["currentHeight"]
		self._currentPrintJobModel.printedHeight = currentHeight + " / " + totalHeightWithExtrusion

	def _createPrintJobModel(self, payload):
		self._currentPrintJobModel = PrintJobModel()
		self._currentPrintJobModel.printStartDateTime = datetime.datetime.now()

		self._currentPrintJobModel.fileOrigin = payload["origin"]
		self._currentPrintJobModel.fileName = payload["name"]
		self._currentPrintJobModel.filePathName = payload["path"]

		# self._file_manager.path_on_disk()
		if "owner" in payload:
			self._currentPrintJobModel.userName = payload["owner"]
		else:
			self._currentPrintJobModel.userName = "John Doe"
		self._currentPrintJobModel.fileSize = payload["size"]

		tempFound = False
		tempTool = 0
		tempBed = 0

		if self._preHeatPluginImplementation != None:
			path_on_disk = octoprint.server.fileManager.path_on_disk(self._currentPrintJobModel.fileOrigin, self._currentPrintJobModel.filePathName)

			preHeatTemperature = self._preHeatPluginImplementation.read_temperatures_from_file(path_on_disk)
			if preHeatTemperature != None:
				if "bed" in preHeatTemperature:
					tempBed = preHeatTemperature["bed"]
					tempFound = True
				if "tool0" in preHeatTemperature:
					tempTool = preHeatTemperature["tool0"]
					tempFound = True
			pass

		else:
			currentTemps = self._printer.get_current_temperatures()
			if ( currentTemps != None and  "bed" in currentTemps and "tool0" in currentTemps ):
				tempBed = currentTemps["bed"]["target"]
				tempTool = currentTemps["tool0"]["target"]
				tempFound = True

		if (tempFound == True):
			tempModel = TemperatureModel()
			tempModel.sensorName = "bed"
			tempModel.sensorValue = tempBed
			self._currentPrintJobModel.addTemperatureModel(tempModel)

			tempModel = TemperatureModel()
			tempModel.sensorName = "tool0"
			tempModel.sensorValue = tempTool
			self._currentPrintJobModel.addTemperatureModel(tempModel)

	def _printJobFinished(self, printStatus, payload):
		self._currentPrintJobModel.printEndDateTime = datetime.datetime.now()
		self._currentPrintJobModel.duration = (self._currentPrintJobModel.printEndDateTime - self._currentPrintJobModel.printStartDateTime).total_seconds()
		self._currentPrintJobModel.printStatusResult = printStatus

		if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT]):
			self._cameraManager.takeSnapshotAsync(CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime))

		if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TAKE_ULTIMAKER_THUMBNAIL_AFTER_PRINT]):
			# check if available
			if (not self._ultimakerFormatPluginImplementation == None):
				self._cameraManager.takeUltimakerPackageThumbnailAsync(CameraManager.buildSnapshotFilename(self._currentPrintJobModel.printStartDateTime), self._currentPrintJobModel.fileName)
			else:
				self._logger.error("UltimakerPackageFormat Thumbnail enabled, but Plugin not available! Activate Plugin-Depenedency check")


		# FilamentInformations e.g. length
		self._createAndAssignFilamentModel(self._currentPrintJobModel, payload)

		# store everything in the database
		databaseId = self._databaseManager.insertPrintJob(self._currentPrintJobModel)

		printJobItem = None
		if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT]):
			self._settings.set_int([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID], databaseId)
			self._settings.save()

			# inform client to show job edit dialog
			printJobModel = self._databaseManager.loadPrintJob(databaseId)
			printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel)

		# inform client for a reload
		payload = {
			"action": "printFinished",
			"printJobItem": printJobItem
		}
		self._sendDataToClient(payload)

	######################################################################################### Hooks and public functions



	#######################################################################################   UPLOAD CSV FILE (in Thread)
	def _processCSVUpload(self, path, _sendCSVUploadResultToClient, logger):
		errorCollection = list()
		resultOfPrintJobs = CSVExportImporter.importCSV(path, errorCollection, logger)
		#TODO do something usefull

		_sendCSVUploadResultToClient("my message", errorCollection)
		pass


	@octoprint.plugin.BlueprintPlugin.route("/importCSV", methods=["POST"])
	def post_csvUpload(self):
		input_name = "file"
		input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

		if input_upload_path in flask.request.values:
			# file was uploaded
			sourceLocation = flask.request.values[input_upload_path]

			# because we process in seperate thread we need to create our own temp file, the uploaded temp fiel will be deleted after this request-call
			archive = tempfile.NamedTemporaryFile(delete=False)
			archive.close()
			shutil.copy(sourceLocation, archive.name)
			sourceLocation = archive.name


			thread = threading.Thread(target=self._processCSVUpload,
									  args=(sourceLocation, self._sendCSVUploadResultToClient, self._logger))
			thread.daemon = True
			thread.start()

			# targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename, False)
			# os.rename(sourceLocation, targetLocation)
			pass
		else:
			return flask.make_response("Invalid request, neither a file nor a path of a file to restore provided", 400)


		return flask.jsonify(started=True)

	#######################################################################################   HOOKs
	def on_after_startup(self):
		# check if needed plugins were available
		self._checkForMissingPluginInfos()

	def on_event(self, event, payload):
		# WebBroswer opened
		if Events.CLIENT_OPENED == event:
			# Check if all needed Plugins are available, if not modale dialog to User
			if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK]):
				self._checkForMissingPluginInfos(True)

			if self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT]):
				printJobId = self._settings.get_int([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID])
				if (not printJobId == None):
					try:
						printJobModel = self._databaseManager.loadPrintJob(printJobId)

						printJobItem = TransformPrintJob2JSON.transformPrintJobModel(printJobModel)
						payload = {
							"action": "showPrintJobDialogAfterClientConnection",
							"printJobItem": printJobItem
						}
						self._sendDataToClient(payload)
					except DoesNotExist as e:
						self._settings.remove([SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID])

		elif Events.PRINT_STARTED == event:
			self.alreadyCanceled = False
			self._createPrintJobModel(payload)

		elif "DisplayLayerProgress_layerChanged" == event or event == "DisplayLayerProgress_heightChanged":
			self._updatePrintJobModelWithLayerHeightInfos(payload)

		elif Events.PRINT_DONE == event:
			self._printJobFinished("success", payload)
		elif Events.PRINT_FAILED == event:
			if self.alreadyCanceled == False:
				self._printJobFinished("failed", payload)
		elif Events.PRINT_CANCELLED == event:
			self.alreadyCanceled = True
			self._printJobFinished("canceled", payload)

		pass

	def on_settings_save(self, data):
		# default save function
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

	# to allow the frontend to trigger an GET call
	def on_api_get(self, request):
		if len(request.values) != 0:
			action = request.values["action"]
		pass

	##~~ SettingsPlugin mixin
	def get_settings_defaults(self):
		settings = dict()
		settings[SettingsKeys.SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK] = True
		settings[SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT] = True
		settings[SettingsKeys.SETTINGS_KEY_SHOW_PRINTJOB_DIALOG_AFTER_PRINT_JOB_ID] = None
		settings[SettingsKeys.SETTINGS_KEY_TAKE_SNAPSHOT_AFTER_PRINT] = True
		settings[SettingsKeys.SETTINGS_KEY_TAKE_ULTIMAKER_THUMBNAIL_AFTER_PRINT] = True

		settings[SettingsKeys.SETTINGS_KEY_DATABASE_PATH] = ""
		settings[SettingsKeys.SETTINGS_KEY_SNAPSHOT_PATH] = ""

		return settings

	##~~ TemplatePlugin mixin
	def get_template_configs(self):
		return [
			dict(type="tab", name="Print Job History"),
			dict(type="settings", custom_bindings=True)
		]

	def get_template_vars(self):
		return dict(
			apikey = self._settings.global_get(["api","key"])
		)

	##~~ AssetPlugin mixin
	def get_assets(self):
		# Define your plugin's asset files to automatically include in the
		# core UI here.
		return dict(
			js=["js/PrintJobHistory.js",
				"js/PrintJobHistory-APIClient.js",
				"js/PrintJobHistory-PluginCheckDialog.js",
				"js/PrintJobHistory-EditJobDialog.js",
				"js/PrintJobHistory-ComponentFactory.js",
				"js/quill.min.js",
				"js/TableItemHelper.js"],
			css=["css/PrintJobHistory.css",
				 "css/quill.snow.css"],
			less=["less/PrintJobHistory.less"]
		)

	##~~ Softwareupdate hook
	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
		# for details.
		return dict(
			PrintJobHistory=dict(
				displayName="PrintJobHistory Plugin",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="OllisGit",
				repo="OctoPrint-PrintJobHistory",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/OllisGit/OctoPrint-PrintJobHistory/releases/latest/download/master.zip"
			)
		)

	# Increase upload-size (default 100kb) for uploading images
	def bodysize_hook(self, current_max_body_sizes, *args, **kwargs):
		return [("POST", r"/upload/", 5 * 1024 * 1024)]	# size in bytes


	# For Streaming I need a special ResponseHandler
	def route_hook(self, server_routes, *args, **kwargs):
		from octoprint.server.util.tornado import LargeResponseHandler, UrlProxyHandler, path_validation_factory
		from octoprint.util import is_hidden_path

		return [
			# (r'myvideofeed', StreamHandler, dict(url=self._settings.global_get(["webcam", "snapshot"]),
			# 									 as_attachment=True)),
			(r"mysnapshot", UrlProxyHandler, dict(url=self._settings.global_get(["webcam", "snapshot"]),
												 as_attachment=True))
		]


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
# Name is used in the left Settings-Menue
__plugin_name__ = "PrintJobHistory"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = PrintJobHistoryPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.server.http.routes": __plugin_implementation__.route_hook,
		"octoprint.server.http.bodysize": __plugin_implementation__.bodysize_hook,
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}



