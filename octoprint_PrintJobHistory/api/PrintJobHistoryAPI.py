# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from flask import jsonify, request, make_response, Response, send_file
import flask

import json

import os

from datetime import datetime

from octoprint_PrintJobHistory import PrintJobModel, CameraManager
from octoprint_PrintJobHistory.api import TransformPrintJob2JSON

from octoprint_PrintJobHistory.common.SettingsKeys import SettingsKeys

from octoprint_PrintJobHistory.CameraManager import CameraManager
from octoprint_PrintJobHistory.common import CSVExportImporter


class PrintJobHistoryAPI(octoprint.plugin.BlueprintPlugin):


	def _updatePrintJobFromJson(self, printJobModel,  jsonData):

		# some attributes shouldn't be changed via the UI (API)
		# printJobModel.userName = self._getValueFromDictOrNone("userName", jsonData)
		# printJobModel.printStartDateTime = datetime.strptime(jsonData["printStartDateTimeFormatted"], "%d.%m.%Y %H:%M")
		# printJobModel.printEndDateTime = datetime.strptime(jsonData["printEndDateTimeFormatted"], "%d.%m.%Y %H:%M")
		# printJobModel.printStatusResult = self._getValueFromDictOrNone("printStatusResult", jsonData)
		# printJobModel.fileName = self._getValueFromDictOrNone("fileName", jsonData)
		# printJobModel.filePathName = self._getValueFromDictOrNone("filePathName", jsonData)
		# printJobModel.fileSize = self._getValueFromDictOrNone("fileSize", jsonData)

		# changable...
		printJobModel.noteText = self._getValueFromDictOrNone("noteText", jsonData)
		printJobModel.noteDeltaFormat = json.dumps(self._getValueFromDictOrNone("noteDeltaFormat", jsonData))
		printJobModel.noteHtml = self._getValueFromDictOrNone("noteHtml", jsonData)
		printJobModel.printedLayers = self._getValueFromDictOrNone("printedLayers", jsonData)
		printJobModel.printedHeight = self._getValueFromDictOrNone("printedHeight", jsonData)

		filamentModel = printJobModel.loadFilamentFromAssoziation()
		filamentModel.spoolName = self._getValueFromDictOrNone("spoolName", jsonData)
		filamentModel.material = self._getValueFromDictOrNone("material", jsonData)
		filamentModel.usedLength = self._convertM2MM(self._getValueFromDictOrNone("usedLength", jsonData))
		filamentModel.calculatedLength = self._convertM2MM(self._getValueFromDictOrNone("calculatedLength", jsonData))
		filamentModel.usedWeight = self._getValueFromDictOrNone("usedWeight", jsonData)
		filamentModel.usedCost = self._getValueFromDictOrNone("usedCost", jsonData)

		# temperatureModel = TemperatureModel

		return printJobModel

	def _getValueFromDictOrNone(self, key, values):
		if key in values:
			return values[key]
		return None

	#  convert m to mm
	def _convertM2MM(self, value):
		if (value == None or value == ""):
			return 0.0
		floatValue = float(value)
		return floatValue * 1000.0

################################################### APIs



	#######################################################################################   DEACTIVATE PLUGIN CHECK
	@octoprint.plugin.BlueprintPlugin.route("/deactivatePluginCheck", methods=["PUT"])
	def put_pluginDependencyCheck(self):
		self._settings.setBoolean([SettingsKeys.SETTINGS_KEY_PLUGIN_DEPENDENCY_CHECK], False)
		self._settings.save()

		return flask.jsonify([])


	#######################################################################################   LOAD ALL JOBS BY QUERY
	@octoprint.plugin.BlueprintPlugin.route("/loadPrintJobHistoryByQuery", methods=["GET"])
	def get_printjobhistoryByQuery(self):

		tableQuery = flask.request.values
		allJobsModels = self._databaseManager.loadPrintJobsByQuery(tableQuery)
		# allJobsAsDict = self._convertPrintJobHistoryModelsToDict(allJobsModels)
		allJobsAsDict = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels)

		totalItemCount = self._databaseManager.countPrintJobsByQuery(tableQuery)
		return flask.jsonify({
								"totalItemCount": totalItemCount,
								"allPrintJobs": allJobsAsDict
							})

	#######################################################################################   DELETE JOB
	@octoprint.plugin.BlueprintPlugin.route("/removePrintJob/<int:databaseId>", methods=["DELETE"])
	def delete_printjob(self, databaseId):
		printJob = self._databaseManager.loadPrintJob(databaseId)
		snapshotFilename = CameraManager.buildSnapshotFilename(printJob.printStartDateTime)
		self._cameraManager.deleteSnapshot(snapshotFilename)
		self._databaseManager.deletePrintJob(databaseId)
		return flask.jsonify()

	#######################################################################################   UPDATE JOB
	@octoprint.plugin.BlueprintPlugin.route("/updatePrintJob/<int:databaseId>", methods=["PUT"])
	def put_printjob(self, databaseId):
		jsonData = request.json
		printJobModel = self._databaseManager.loadPrintJob(databaseId)
		self._updatePrintJobFromJson(printJobModel, jsonData)
		self._databaseManager.updatePrintJob(printJobModel)
		# response = self.get_printjobhistory()
		# return response
		return flask.jsonify()

	#######################################################################################   GET SNAPSHOT
	@octoprint.plugin.BlueprintPlugin.route("/printJobSnapshot/<string:snapshotFilename>", methods=["GET"])
	def get_snapshot(self, snapshotFilename):
		absoluteFilename = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename)
		return send_file(absoluteFilename, mimetype='image/jpg')

	#######################################################################################   TAKE SNAPSHOT
	@octoprint.plugin.BlueprintPlugin.route("/takeSnapshot/<string:snapshotFilename>", methods=["PUT"])
	def put_snapshot(self, snapshotFilename):
		self._cameraManager.takeSnapshot(snapshotFilename)
		return flask.jsonify({
			"snapshotFilename": snapshotFilename
		})

	#######################################################################################   UPLOAD SNAPSHOT
	@octoprint.plugin.BlueprintPlugin.route("/upload/snapshot/<string:snapshotFilename>", methods=["POST"])
	def post_snapshot(self, snapshotFilename):

		input_name = "file"
		input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

		if input_upload_path in flask.request.values:
			# file was uploaded
			sourceLocation = flask.request.values[input_upload_path]
			targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename, False)
			os.rename(sourceLocation, targetLocation)
			pass

		return flask.jsonify({
			"snapshotFilename": snapshotFilename
		})	\

	#######################################################################################   DELETE SNAPSHOT
	@octoprint.plugin.BlueprintPlugin.route("/deleteSnapshotImage/<string:snapshotFilename>", methods=["DELETE"])
	def delete_snapshot(self, snapshotFilename):

		self._cameraManager.deleteSnapshot(snapshotFilename)

		# input_name = "file"
		# input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])
		#
		# if input_upload_path in flask.request.values:
		# 	# file to restore was uploaded
		# 	sourceLocation = flask.request.values[input_upload_path]
		# 	targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename)
		# 	os.rename(sourceLocation, targetLocation)
		# 	pass

		return flask.jsonify({
			"snapshotFilename": snapshotFilename
		})


	#######################################################################################   DOWNLOAD DATABASE
	@octoprint.plugin.BlueprintPlugin.route("/downloadDatabase", methods=["GET"])
	def download_database(self):
		return send_file(self._databaseManager.getDatabaseFileLocation(),
						 mimetype='application/octet-stream',
						 attachment_filename='printJobHistory.db',
						 as_attachment=True)


	#######################################################################################   DELETE DATABASE
	@octoprint.plugin.BlueprintPlugin.route("/deleteDatabase", methods=["DELETE"])
	def delete_database(self):

		self._databaseManager.recreateDatabase()

		return flask.jsonify({
			"result": "success"
		})


	@octoprint.plugin.BlueprintPlugin.route("/exportPrintJobHistory/<string:exportType>", methods=["GET"])
	def exportPrintJobHistoryData(self, exportType):

		if exportType == "CSV":
			allJobsModels = self._databaseManager.loadAllPrintJobs()
			# allJobsDict = self._convertPrintJobHistoryEntitiesToDict(allJobsEntities)
			allJobsDict = TransformPrintJob2JSON.transformAllPrintJobModels(allJobsModels)

			# csvContent = Transform2CSV.transform2CSV(allJobsDict)
			csvContent = CSVExportImporter.transform2CSV(allJobsDict)

			response = flask.make_response(csvContent)
			response.headers["Content-type"] = "text/csv"
			response.headers["Content-Disposition"] = "attachment; filename=OctoprintPrintJobHistory.csv" # TODO add timestamp


			return response
		else:
			print("BOOOMM not supported type")

		pass










