"""Nested Blender collection helper for the import outliner hierarchy.

Behavior-preserving port of core.py's CollectionWrapper: builds a tree of collections, links
objects into leaves, and alphabetically sorts the tree at the end so the outliner is stable.
"""

import bpy


class CollectionWrapper:
    def __init__(self, name, postfix=None, parent=None, visible=True):
        if parent is None:
            parent = bpy.context.view_layer.layer_collection
        if postfix is None:
            self.postfix = " - " + name
        else:
            self.postfix = postfix + " " + name
            name += postfix
        self.children = {}
        collection = bpy.data.collections.new(name)
        parent.collection.children.link(collection)
        self.layer_collection = None
        for layer_collection in parent.children:
            if layer_collection.collection == collection:
                self.layer_collection = layer_collection
                break
        self.layer_collection.hide_viewport = not visible

    def add(self, obj):
        self.layer_collection.collection.objects.link(obj)

    def open(self, name, visible=True):
        if name in self.children:
            return self.children[name]
        child = CollectionWrapper(name, self.postfix, self.layer_collection, visible)
        self.children[name] = child
        return child

    def sort(self):
        children = self.layer_collection.collection.children
        sorted_children = sorted(self.children.items(), key=lambda a: a[0].lower())
        for (_, child) in sorted_children:
            child.sort()
            collection = child.layer_collection.collection
            children.unlink(collection)
            children.link(collection)
