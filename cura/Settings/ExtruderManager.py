# Copyright (c) 2017 Ultimaker B.V.
# Cura is released under the terms of the AGPLv3 or higher.

from PyQt5.QtCore import pyqtSignal, pyqtProperty, QObject, QVariant #For communicating data and events to Qt.
from UM.FlameProfiler import pyqtSlot

from UM.Application import Application #To get the global container stack to find the current machine.
from UM.Logger import Logger
from UM.Decorators import deprecated
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNode import SceneNode
from UM.Scene.Selection import Selection
from UM.Scene.Iterator.BreadthFirstIterator import BreadthFirstIterator
from UM.Settings.ContainerRegistry import ContainerRegistry #Finding containers by ID.
from UM.Settings.InstanceContainer import InstanceContainer
from UM.Settings.SettingFunction import SettingFunction
from UM.Settings.ContainerStack import ContainerStack
from UM.Settings.Interfaces import DefinitionContainerInterface
from typing import Optional, List, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from cura.Settings.ExtruderStack import ExtruderStack
    from cura.Settings.GlobalStack import GlobalStack


##  Manages all existing extruder stacks.
#
#   This keeps a list of extruder stacks for each machine.
class ExtruderManager(QObject):
    ##  Signal to notify other components when the list of extruders for a machine definition changes.
    extrudersChanged = pyqtSignal(QVariant)

    ## Signal to notify other components when the global container stack is switched to a definition
    #  that has different extruders than the previous global container stack
    globalContainerStackDefinitionChanged = pyqtSignal()

    ##  Notify when the user switches the currently active extruder.
    activeExtruderChanged = pyqtSignal()

    ##  Registers listeners and such to listen to changes to the extruders.
    def __init__(self, parent = None):
        super().__init__(parent)
        self._extruder_trains = { } #Per machine, a dictionary of extruder container stack IDs. Only for separately defined extruders.
        self._active_extruder_index = 0
        self._selected_object_extruders = []
        Application.getInstance().globalContainerStackChanged.connect(self.__globalContainerStackChanged)
        self._global_container_stack_definition_id = None
        self._addCurrentMachineExtruders()

        Selection.selectionChanged.connect(self.resetSelectedObjectExtruders)

    ##  Gets the unique identifier of the currently active extruder stack.
    #
    #   The currently active extruder stack is the stack that is currently being
    #   edited.
    #
    #   \return The unique ID of the currently active extruder stack.
    @pyqtProperty(str, notify = activeExtruderChanged)
    def activeExtruderStackId(self) -> Optional[str]:
        if not Application.getInstance().getGlobalContainerStack():
            return None # No active machine, so no active extruder.
        try:
            return self._extruder_trains[Application.getInstance().getGlobalContainerStack().getId()][str(self._active_extruder_index)].getId()
        except KeyError: # Extruder index could be -1 if the global tab is selected, or the entry doesn't exist if the machine definition is wrong.
            return None

    ##  Return extruder count according to extruder trains.
    @pyqtProperty(int, notify = extrudersChanged)
    def extruderCount(self):
        if not Application.getInstance().getGlobalContainerStack():
            return 0  # No active machine, so no extruders.
        try:
            return len(self._extruder_trains[Application.getInstance().getGlobalContainerStack().getId()])
        except KeyError:
            return 0

    @pyqtProperty("QVariantMap", notify = extrudersChanged)
    def extruderIds(self):
        map = {}
        global_stack_id = Application.getInstance().getGlobalContainerStack().getId()
        for position in self._extruder_trains[global_stack_id]:
            map[position] = self._extruder_trains[global_stack_id][position].getId()
        return map

    @pyqtSlot(str, result = str)
    def getQualityChangesIdByExtruderStackId(self, id: str) -> str:
        for position in self._extruder_trains[Application.getInstance().getGlobalContainerStack().getId()]:
            extruder = self._extruder_trains[Application.getInstance().getGlobalContainerStack().getId()][position]
            if extruder.getId() == id:
                return extruder.qualityChanges.getId()

    ##  The instance of the singleton pattern.
    #
    #   It's None if the extruder manager hasn't been created yet.
    __instance = None

    ##  Gets an instance of the extruder manager, or creates one if no instance
    #   exists yet.
    #
    #   This is an implementation of singleton. If an extruder manager already
    #   exists, it is re-used.
    #
    #   \return The extruder manager.
    @classmethod
    def getInstance(cls) -> "ExtruderManager":
        if not cls.__instance:
            cls.__instance = ExtruderManager()
        return cls.__instance

    ##  Changes the active extruder by index.
    #
    #   \param index The index of the new active extruder.
    @pyqtSlot(int)
    def setActiveExtruderIndex(self, index: int) -> None:
        self._active_extruder_index = index
        self.activeExtruderChanged.emit()

    @pyqtProperty(int, notify = activeExtruderChanged)
    def activeExtruderIndex(self) -> int:
        return self._active_extruder_index

    ##  Gets the extruder name of an extruder of the currently active machine.
    #
    #   \param index The index of the extruder whose name to get.
    @pyqtSlot(int, result = str)
    def getExtruderName(self, index):
        try:
            return list(self.getActiveExtruderStacks())[index].getName()
        except IndexError:
            return ""

    ## Emitted whenever the selectedObjectExtruders property changes.
    selectedObjectExtrudersChanged = pyqtSignal()

    ##  Provides a list of extruder IDs used by the current selected objects.
    @pyqtProperty("QVariantList", notify = selectedObjectExtrudersChanged)
    def selectedObjectExtruders(self) -> List[str]:
        if not self._selected_object_extruders:
            object_extruders = set()

            # First, build a list of the actual selected objects (including children of groups, excluding group nodes)
            selected_nodes = []
            for node in Selection.getAllSelectedObjects():
                if node.callDecoration("isGroup"):
                    for grouped_node in BreadthFirstIterator(node):
                        if grouped_node.callDecoration("isGroup"):
                            continue

                        selected_nodes.append(grouped_node)
                else:
                    selected_nodes.append(node)

            # Then, figure out which nodes are used by those selected nodes.
            global_stack = Application.getInstance().getGlobalContainerStack()
            current_extruder_trains = self._extruder_trains.get(global_stack.getId())
            for node in selected_nodes:
                extruder = node.callDecoration("getActiveExtruder")
                if extruder:
                    object_extruders.add(extruder)
                elif current_extruder_trains:
                    object_extruders.add(current_extruder_trains["0"].getId())

            self._selected_object_extruders = list(object_extruders)

        return self._selected_object_extruders

    ##  Reset the internal list used for the selectedObjectExtruders property
    #
    #   This will trigger a recalculation of the extruders used for the
    #   selection.
    def resetSelectedObjectExtruders(self) -> None:
        self._selected_object_extruders = []
        self.selectedObjectExtrudersChanged.emit()

    def getActiveExtruderStack(self) -> Optional["ExtruderStack"]:
        global_container_stack = Application.getInstance().getGlobalContainerStack()

        if global_container_stack:
            if global_container_stack.getId() in self._extruder_trains:
                if str(self._active_extruder_index) in self._extruder_trains[global_container_stack.getId()]:
                    return self._extruder_trains[global_container_stack.getId()][str(self._active_extruder_index)]
        return None

    ##  Get an extruder stack by index
    def getExtruderStack(self, index) -> Optional["ExtruderStack"]:
        global_container_stack = Application.getInstance().getGlobalContainerStack()
        if global_container_stack:
            if global_container_stack.getId() in self._extruder_trains:
                if str(index) in self._extruder_trains[global_container_stack.getId()]:
                    return self._extruder_trains[global_container_stack.getId()][str(index)]
        return None

    ##  Get all extruder stacks
    def getExtruderStacks(self) -> List["ExtruderStack"]:
        result = []
        for i in range(self.extruderCount):
            result.append(self.getExtruderStack(i))
        return result

    ##  Adds all extruders of a specific machine definition to the extruder
    #   manager.
    #
    #   \param machine_definition   The machine definition to add the extruders for.
    #   \param machine_id           The machine_id to add the extruders for.
    @deprecated("Use CuraStackBuilder", "2.6")
    def addMachineExtruders(self, machine_definition: DefinitionContainerInterface, machine_id: str) -> None:
        changed = False
        machine_definition_id = machine_definition.getId()
        if machine_id not in self._extruder_trains:
            self._extruder_trains[machine_id] = { }
            changed = True
        container_registry = ContainerRegistry.getInstance()
        if container_registry:
            # Add the extruder trains that don't exist yet.
            for extruder_definition in container_registry.findDefinitionContainers(machine = machine_definition_id):
                position = extruder_definition.getMetaDataEntry("position", None)
                if not position:
                    Logger.log("w", "Extruder definition %s specifies no position metadata entry.", extruder_definition.getId())
                if not container_registry.findContainerStacks(machine = machine_id, position = position): # Doesn't exist yet.
                    self.createExtruderTrain(extruder_definition, machine_definition, position, machine_id)
                    changed = True

            # Gets the extruder trains that we just created as well as any that still existed.
            extruder_trains = container_registry.findContainerStacks(type = "extruder_train", machine = machine_id)
            for extruder_train in extruder_trains:
                self._extruder_trains[machine_id][extruder_train.getMetaDataEntry("position")] = extruder_train

                # regardless of what the next stack is, we have to set it again, because of signal routing.
                extruder_train.setNextStack(Application.getInstance().getGlobalContainerStack())
                changed = True
        if changed:
            self.extrudersChanged.emit(machine_id)

    def registerExtruder(self, extruder_train, machine_id):
        changed = False

        if machine_id not in self._extruder_trains:
            self._extruder_trains[machine_id] = {}
            changed = True

        # do not register if an extruder has already been registered at the position on this machine
        if any(item.getId() == extruder_train.getId() for item in self._extruder_trains[machine_id].values()):
            Logger.log("w", "Extruder [%s] has already been registered on machine [%s], not doing anything",
                       extruder_train.getId(), machine_id)
            return

        if extruder_train:
            self._extruder_trains[machine_id][extruder_train.getMetaDataEntry("position")] = extruder_train
            changed = True
        if changed:
            self.extrudersChanged.emit(machine_id)

    ##  Creates a container stack for an extruder train.
    #
    #   The container stack has an extruder definition at the bottom, which is
    #   linked to a machine definition. Then it has a variant profile, a material
    #   profile, a quality profile and a user profile, in that order.
    #
    #   The resulting container stack is added to the registry.
    #
    #   \param extruder_definition  The extruder to create the extruder train for.
    #   \param machine_definition   The machine that the extruder train belongs to.
    #   \param position             The position of this extruder train in the extruder slots of the machine.
    #   \param machine_id           The id of the "global" stack this extruder is linked to.
    @deprecated("Use CuraStackBuilder::createExtruderStack", "2.6")
    def createExtruderTrain(self, extruder_definition: DefinitionContainerInterface, machine_definition: DefinitionContainerInterface,
                            position, machine_id: str) -> None:
        # Cache some things.
        container_registry = ContainerRegistry.getInstance()
        machine_definition_id = Application.getInstance().getMachineManager().getQualityDefinitionId(machine_definition)

        # Create a container stack for this extruder.
        extruder_stack_id = container_registry.uniqueName(extruder_definition.getId())
        container_stack = ContainerStack(extruder_stack_id)
        container_stack.setName(extruder_definition.getName())  # Take over the display name to display the stack with.
        container_stack.addMetaDataEntry("type", "extruder_train")
        container_stack.addMetaDataEntry("machine", machine_id)
        container_stack.addMetaDataEntry("position", position)
        container_stack.addContainer(extruder_definition)

        # Find the variant to use for this extruder.
        variant = container_registry.findInstanceContainers(id = "empty_variant")[0]
        if machine_definition.getMetaDataEntry("has_variants"):
            # First add any variant. Later, overwrite with preference if the preference is valid.
            variants = container_registry.findInstanceContainers(definition = machine_definition_id, type = "variant")
            if len(variants) >= 1:
                variant = variants[0]
            preferred_variant_id = machine_definition.getMetaDataEntry("preferred_variant")
            if preferred_variant_id:
                preferred_variants = container_registry.findInstanceContainers(id = preferred_variant_id, definition = machine_definition_id, type = "variant")
                if len(preferred_variants) >= 1:
                    variant = preferred_variants[0]
                else:
                    Logger.log("w", "The preferred variant \"%s\" of machine %s doesn't exist or is not a variant profile.", preferred_variant_id, machine_id)
                    # And leave it at the default variant.
        container_stack.addContainer(variant)

        # Find a material to use for this variant.
        material = container_registry.findInstanceContainers(id = "empty_material")[0]
        if machine_definition.getMetaDataEntry("has_materials"):
            # First add any material. Later, overwrite with preference if the preference is valid.
            machine_has_variant_materials = machine_definition.getMetaDataEntry("has_variant_materials", default = False)
            if machine_has_variant_materials or machine_has_variant_materials == "True":
                materials = container_registry.findInstanceContainers(type = "material", definition = machine_definition_id, variant = variant.getId())
            else:
                materials = container_registry.findInstanceContainers(type = "material", definition = machine_definition_id)
            if len(materials) >= 1:
                material = materials[0]
            preferred_material_id = machine_definition.getMetaDataEntry("preferred_material")
            if preferred_material_id:
                global_stack = ContainerRegistry.getInstance().findContainerStacks(id = machine_id)
                if global_stack:
                    approximate_material_diameter = round(global_stack[0].getProperty("material_diameter", "value"))
                else:
                    approximate_material_diameter = round(machine_definition.getProperty("material_diameter", "value"))

                search_criteria = { "type": "material",  "id": preferred_material_id, "approximate_diameter": approximate_material_diameter}
                if machine_definition.getMetaDataEntry("has_machine_materials"):
                    search_criteria["definition"] = machine_definition_id

                    if machine_definition.getMetaDataEntry("has_variants") and variant:
                        search_criteria["variant"] = variant.id
                else:
                    search_criteria["definition"] = "fdmprinter"

                preferred_materials = container_registry.findInstanceContainers(**search_criteria)
                if len(preferred_materials) >= 1:
                    # In some cases we get multiple materials. In that case, prefer materials that are marked as read only.
                    read_only_preferred_materials = [preferred_material for preferred_material in preferred_materials if preferred_material.isReadOnly()]
                    if len(read_only_preferred_materials) >= 1:
                        material = read_only_preferred_materials[0]
                    else:
                        material = preferred_materials[0]
                else:
                    Logger.log("w", "The preferred material \"%s\" of machine %s doesn't exist or is not a material profile.", preferred_material_id, machine_id)
                    # And leave it at the default material.
        container_stack.addContainer(material)

        # Find a quality to use for this extruder.
        quality = container_registry.getEmptyInstanceContainer()

        search_criteria = { "type": "quality" }
        if machine_definition.getMetaDataEntry("has_machine_quality"):
            search_criteria["definition"] = machine_definition_id
            if machine_definition.getMetaDataEntry("has_materials") and material:
                search_criteria["material"] = material.id
        else:
            search_criteria["definition"] = "fdmprinter"

        preferred_quality = machine_definition.getMetaDataEntry("preferred_quality")
        if preferred_quality:
            search_criteria["id"] = preferred_quality

        containers = ContainerRegistry.getInstance().findInstanceContainers(**search_criteria)
        if not containers and preferred_quality:
            Logger.log("w", "The preferred quality \"%s\" of machine %s doesn't exist or is not a quality profile.", preferred_quality, machine_id)
            search_criteria.pop("id", None)
            containers = ContainerRegistry.getInstance().findInstanceContainers(**search_criteria)
        if containers:
            quality = containers[0]

        container_stack.addContainer(quality)

        empty_quality_changes = container_registry.findInstanceContainers(id = "empty_quality_changes")[0]
        container_stack.addContainer(empty_quality_changes)

        user_profile = container_registry.findInstanceContainers(type = "user", extruder = extruder_stack_id)
        if user_profile: # There was already a user profile, loaded from settings.
            user_profile = user_profile[0]
        else:
            user_profile = InstanceContainer(extruder_stack_id + "_current_settings")  # Add an empty user profile.
            user_profile.addMetaDataEntry("type", "user")
            user_profile.addMetaDataEntry("extruder", extruder_stack_id)
            from cura.CuraApplication import CuraApplication
            user_profile.addMetaDataEntry("setting_version", CuraApplication.SettingVersion)
            user_profile.setDefinition(machine_definition)
            container_registry.addContainer(user_profile)
        container_stack.addContainer(user_profile)

        # regardless of what the next stack is, we have to set it again, because of signal routing.
        container_stack.setNextStack(Application.getInstance().getGlobalContainerStack())

        container_registry.addContainer(container_stack)

    def getAllExtruderValues(self, setting_key):
        return self.getAllExtruderSettings(setting_key, "value")

    ##  Gets a property of a setting for all extruders.
    #
    #   \param setting_key  \type{str} The setting to get the property of.
    #   \param property  \type{str} The property to get.
    #   \return \type{List} the list of results
    def getAllExtruderSettings(self, setting_key, property):
        global_container_stack = Application.getInstance().getGlobalContainerStack()
        if global_container_stack.getProperty("machine_extruder_count", "value") <= 1:
            return [global_container_stack.getProperty(setting_key, property)]

        result = []
        for index in self.extruderIds:
            extruder_stack_id = self.extruderIds[str(index)]
            stack = ContainerRegistry.getInstance().findContainerStacks(id = extruder_stack_id)[0]
            result.append(stack.getProperty(setting_key, property))
        return result

    ##  Gets the extruder stacks that are actually being used at the moment.
    #
    #   An extruder stack is being used if it is the extruder to print any mesh
    #   with, or if it is the support infill extruder, the support interface
    #   extruder, or the bed adhesion extruder.
    #
    #   If there are no extruders, this returns the global stack as a singleton
    #   list.
    #
    #   \return A list of extruder stacks.
    def getUsedExtruderStacks(self) -> List["ContainerStack"]:
        global_stack = Application.getInstance().getGlobalContainerStack()
        container_registry = ContainerRegistry.getInstance()

        if global_stack.getProperty("machine_extruder_count", "value") <= 1: #For single extrusion.
            return [global_stack]

        used_extruder_stack_ids = set()

        #Get the extruders of all meshes in the scene.
        support_enabled = False
        support_bottom_enabled = False
        support_roof_enabled = False
        scene_root = Application.getInstance().getController().getScene().getRoot()
        meshes = [node for node in DepthFirstIterator(scene_root) if type(node) is SceneNode and node.isSelectable()] #Only use the nodes that will be printed.
        for mesh in meshes:
            extruder_stack_id = mesh.callDecoration("getActiveExtruder")
            if not extruder_stack_id: #No per-object settings for this node.
                extruder_stack_id = self.extruderIds["0"]
            used_extruder_stack_ids.add(extruder_stack_id)

            #Get whether any of them use support.
            per_mesh_stack = mesh.callDecoration("getStack")
            if per_mesh_stack:
                support_enabled |= per_mesh_stack.getProperty("support_enable", "value")
                support_bottom_enabled |= per_mesh_stack.getProperty("support_bottom_enable", "value")
                support_roof_enabled |= per_mesh_stack.getProperty("support_roof_enable", "value")
            else: #Take the setting from the build extruder stack.
                extruder_stack = container_registry.findContainerStacks(id = extruder_stack_id)[0]
                support_enabled |= extruder_stack.getProperty("support_enable", "value")
                support_bottom_enabled |= extruder_stack.getProperty("support_bottom_enable", "value")
                support_roof_enabled |= extruder_stack.getProperty("support_roof_enable", "value")

        #The support extruders.
        if support_enabled:
            used_extruder_stack_ids.add(self.extruderIds[str(global_stack.getProperty("support_infill_extruder_nr", "value"))])
            used_extruder_stack_ids.add(self.extruderIds[str(global_stack.getProperty("support_extruder_nr_layer_0", "value"))])
            if support_bottom_enabled:
                used_extruder_stack_ids.add(self.extruderIds[str(global_stack.getProperty("support_bottom_extruder_nr", "value"))])
            if support_roof_enabled:
                used_extruder_stack_ids.add(self.extruderIds[str(global_stack.getProperty("support_roof_extruder_nr", "value"))])

        #The platform adhesion extruder. Not used if using none.
        if global_stack.getProperty("adhesion_type", "value") != "none":
            used_extruder_stack_ids.add(self.extruderIds[str(global_stack.getProperty("adhesion_extruder_nr", "value"))])
        try:
            return [container_registry.findContainerStacks(id = stack_id)[0] for stack_id in used_extruder_stack_ids]
        except IndexError:  # One or more of the extruders was not found.
            Logger.log("e", "Unable to find one or more of the extruders in %s", used_extruder_stack_ids)
            return []

    ##  Removes the container stack and user profile for the extruders for a specific machine.
    #
    #   \param machine_id The machine to remove the extruders for.
    def removeMachineExtruders(self, machine_id: str):
        for extruder in self.getMachineExtruders(machine_id):
            ContainerRegistry.getInstance().removeContainer(extruder.userChanges.getId())
            ContainerRegistry.getInstance().removeContainer(extruder.getId())
        if machine_id in self._extruder_trains:
            del self._extruder_trains[machine_id]

    ##  Returns extruders for a specific machine.
    #
    #   \param machine_id The machine to get the extruders of.
    def getMachineExtruders(self, machine_id: str):
        if machine_id not in self._extruder_trains:
            return []
        return [self._extruder_trains[machine_id][name] for name in self._extruder_trains[machine_id]]

    ##  Returns a list containing the global stack and active extruder stacks.
    #
    #   The first element is the global container stack, followed by any extruder stacks.
    #   \return \type{List[ContainerStack]}
    def getActiveGlobalAndExtruderStacks(self) -> Optional[List[Union["ExtruderStack", "GlobalStack"]]]:
        global_stack = Application.getInstance().getGlobalContainerStack()
        if not global_stack:
            return None

        result = [global_stack]
        result.extend(self.getActiveExtruderStacks())
        return result

    ##  Returns the list of active extruder stacks.
    #
    #   \return \type{List[ContainerStack]} a list of
    def getActiveExtruderStacks(self) -> List["ExtruderStack"]:
        global_stack = Application.getInstance().getGlobalContainerStack()

        result = []
        if global_stack and global_stack.getId() in self._extruder_trains:
            for extruder in sorted(self._extruder_trains[global_stack.getId()]):
                result.append(self._extruder_trains[global_stack.getId()][extruder])
        return result

    def __globalContainerStackChanged(self) -> None:
        global_container_stack = Application.getInstance().getGlobalContainerStack()
        if global_container_stack and global_container_stack.getBottom() and global_container_stack.getBottom().getId() != self._global_container_stack_definition_id:
            self._global_container_stack_definition_id = global_container_stack.getBottom().getId()
            self.globalContainerStackDefinitionChanged.emit()
        self.activeExtruderChanged.emit()

        self.resetSelectedObjectExtruders()

    ##  Adds the extruders of the currently active machine.
    def _addCurrentMachineExtruders(self) -> None:
        global_stack = Application.getInstance().getGlobalContainerStack()
        if global_stack and global_stack.getBottom():
            self.addMachineExtruders(global_stack.getBottom(), global_stack.getId())

    ##  Get all extruder values for a certain setting.
    #
    #   This is exposed to SettingFunction so it can be used in value functions.
    #
    #   \param key The key of the setting to retrieve values for.
    #
    #   \return A list of values for all extruders. If an extruder does not have a value, it will not be in the list.
    #           If no extruder has the value, the list will contain the global value.
    @staticmethod
    def getExtruderValues(key):
        global_stack = Application.getInstance().getGlobalContainerStack()

        result = []
        for extruder in ExtruderManager.getInstance().getMachineExtruders(global_stack.getId()):
            # only include values from extruders that are "active" for the current machine instance
            if int(extruder.getMetaDataEntry("position")) >= global_stack.getProperty("machine_extruder_count", "value"):
                continue

            value = extruder.getRawProperty(key, "value")

            if value is None:
                continue

            if isinstance(value, SettingFunction):
                value = value(extruder)

            result.append(value)

        if not result:
            result.append(global_stack.getProperty(key, "value"))

        return result

    ##  Get all extruder values for a certain setting.
    #
    #   This is exposed to qml for display purposes
    #
    #   \param key The key of the setting to retieve values for.
    #
    #   \return String representing the extruder values
    @pyqtSlot(str, result="QVariant")
    def getInstanceExtruderValues(self, key):
        return ExtruderManager.getExtruderValues(key)

    ##  Get the value for a setting from a specific extruder.
    #
    #   This is exposed to SettingFunction to use in value functions.
    #
    #   \param extruder_index The index of the extruder to get the value from.
    #   \param key The key of the setting to get the value of.
    #
    #   \return The value of the setting for the specified extruder or for the
    #   global stack if not found.
    @staticmethod
    def getExtruderValue(extruder_index, key):
        extruder = ExtruderManager.getInstance().getExtruderStack(extruder_index)

        if extruder:
            value = extruder.getRawProperty(key, "value")
            if isinstance(value, SettingFunction):
                value = value(extruder)
        else: #Just a value from global.
            value = Application.getInstance().getGlobalContainerStack().getProperty(key, "value")

        return value

    ##  Get the resolve value or value for a given key
    #
    #   This is the effective value for a given key, it is used for values in the global stack.
    #   This is exposed to SettingFunction to use in value functions.
    #   \param key The key of the setting to get the value of.
    #
    #   \return The effective value
    @staticmethod
    def getResolveOrValue(key):
        global_stack = Application.getInstance().getGlobalContainerStack()
        resolved_value = global_stack.getProperty(key, "value")

        return resolved_value
