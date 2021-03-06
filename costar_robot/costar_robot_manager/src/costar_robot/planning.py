'''
(c) 2016 Chris Paxton
'''

import rospy

import PyKDL as kdl
import numpy as np

import tf_conversions.posemath as pm

# import moveit messages
from moveit_msgs.msg import *
from moveit_msgs.srv import *
import actionlib

from pykdl_utils.kdl_parser import kdl_tree_from_urdf_model
from pykdl_utils.kdl_kinematics import KDLKinematics

from trajectory_msgs.msg import JointTrajectory
from trajectory_msgs.msg import JointTrajectoryPoint

class SimplePlanning:
    
    def __init__(self,robot,base_link,end_link,group,
            move_group_ns="move_group",
            planning_scene_topic="planning_scene",
            robot_ns="",
            verbose=False,
            kdl_kin=None,
            joint_names=[]):
        self.robot = robot
        self.tree = kdl_tree_from_urdf_model(self.robot)
        self.chain = self.tree.getChain(base_link, end_link)
        if kdl_kin is None:
          self.kdl_kin = KDLKinematics(self.robot, base_link, end_link)
        else:
          self.kdl_kin = kdl_kin
        self.base_link = base_link
        self.joint_names = joint_names
        self.end_link = end_link
        self.group = group
        self.robot_ns = robot_ns
        self.client = actionlib.SimpleActionClient(move_group_ns, MoveGroupAction)
        self.verbose = verbose
    
    '''
    ik: handles calls to KDL inverse kinematics
    '''
    def ik(self, T, q0, dist=0.5):

      #T = pm.toMatrix(F)
      q = self.kdl_kin.inverse(T,q0)

      # NOTE: this results in unsafe behavior; do not use without checks
      #if q is None:
      #    q = self.kdl_kin.inverse(T)

      return q

    '''
    TODO: finish this
    '''
    def getCartesianMove(self, frame, q0, base_steps=1000, steps_per_meter=1000, vel=1):

      # interpolate between start and goal
      pose = pm.fromMatrix(self.kdl_kin.forward(q0))

      cur_rpy = np.array(pose.M.GetRPY())
      cur_xyz = np.array(pose.p)
      
      goal_rpy = np.array(frame.M.GetRPY())
      goal_xyz = np.array(frame.p)


      steps = base_steps + int((pose.p - frame.p).Norm() * steps_per_meter)
      print " -- Computing %f steps"%steps

      ts = (pose.p - frame.p).Norm() / steps
      traj = JointTrajectory()

      # compute IK
      for i in range(1,steps+1):
        xyz = cur_xyz + ((float(i)/steps) * (goal_xyz - cur_xyz))
        rpy = cur_rpy + ((float(i)/steps) * (goal_rpy - cur_rpy))

        frame = pm.toMatrix(kdl.Frame(kdl.Rotation.RPY(rpy[0],rpy[1],rpy[2]),kdl.Vector(xyz[0],xyz[1],xyz[2])))
        #q = self.kdl_kin.inverse(frame,q0)
        q = self.ik(frame, q0)

        if self.verbose:
          print "%d -- %s %s = %s"%(i,str(xyz),str(rpy),str(q))

        if not q is None:
          pt = JointTrajectoryPoint(positions=q)
          pt.time_from_start = rospy.Duration(i * ts)
          traj.points.append(pt)
          q0 = q
        elif i == steps:
          return JointTrajectory()

      if len(traj.points) < base_steps:
          print rospy.logerr("Planning failure with " \
                  + str(len(traj.points)) \
                  + " / " + str(base_steps) \
                  + " points.")
          return JointTrajectory()

      return traj

    def getGoalConstraints(self, frame, q, timeout=2.0):

        if len(self.robot_ns) > 0:
            srv = rospy.ServiceProxy(self.robot_ns + "/compute_ik", moveit_msgs.srv.GetPositionIK)
        else:
            srv = rospy.ServiceProxy("compute_ik", moveit_msgs.srv.GetPositionIK)

        goal = Constraints()

        #traj = self.getCartesianMove(frame, q)
        #if len(traj.pts) > 0:
        #    joints = traj[-1].positions
        #if joints is None:

        joints = self.kdl_kin.inverse(frame,q)
        print joints

        if joints is None:

            p = geometry_msgs.msg.PoseStamped()
            p.pose.position.x = frame.position.x
            p.pose.position.y = frame.position.y
            p.pose.position.z = frame.position.z
            p.pose.orientation.x = frame.orientation.x
            p.pose.orientation.y = frame.orientation.y
            p.pose.orientation.z = frame.orientation.z
            p.pose.orientation.w = frame.orientation.w
            p.header.frame_id = "/world"

            print p

            ik_req = moveit_msgs.msg.PositionIKRequest()
            ik_req.robot_state.joint_state.name = self.joint_names
            ik_req.robot_state.joint_state.position = q
            ik_req.avoid_collisions = True
            ik_req.timeout = rospy.Duration(timeout)
            ik_req.attempts = 5
            ik_req.group_name = self.group
            ik_req.pose_stamped = p

            rospy.logwarn("Getting IK position...")
            ik_resp = srv(ik_req)

            rospy.logwarn("IK RESULT ERROR CODE = %d"%(ik_resp.error_code.val))

            #if ik_resp.error_code.val > 0:
            #  return (ik_resp, None)
            #print ik_resp.solution

            ###############################
            # now create the goal based on inverse kinematics

            if not ik_resp.error_code.val < 0:
                for i in range(0,len(ik_resp.solution.joint_state.name)):
                    print ik_resp.solution.joint_state.name[i]
                    print ik_resp.solution.joint_state.position[i]
                    joint = JointConstraint()
                    joint.joint_name = ik_resp.solution.joint_state.name[i]
                    joint.position = ik_resp.solution.joint_state.position[i] 
                    joint.tolerance_below = 0.005
                    joint.tolerance_above = 0.005
                    joint.weight = 1.0
                    goal.joint_constraints.append(joint)

            return (ik_resp, goal)

        else:
          for i in range(0,len(self.joint_names)):
                print self.joint_names[i]
                print joints[i]
                joint = JointConstraint()
                joint.joint_name = self.joint_names[i]
                joint.position = joints[i] 
                joint.tolerance_below = 0.005
                joint.tolerance_above = 0.005
                joint.weight = 1.0
                goal.joint_constraints.append(joint)

          return (None, goal)

    def getPlan(self,frame,q,compute_ik=True):
        planning_options = PlanningOptions()
        planning_options.plan_only = False
        planning_options.replan = False
        planning_options.replan_attempts = 0
        planning_options.replan_delay = 0.1
        planning_options.planning_scene_diff.is_diff = True
        planning_options.planning_scene_diff.robot_state.is_diff = True

        motion_req = MotionPlanRequest()

        motion_req.start_state.joint_state.position = q
        motion_req.workspace_parameters.header.frame_id = self.base_link
        motion_req.workspace_parameters.max_corner.x = 1.0
        motion_req.workspace_parameters.max_corner.y = 1.0
        motion_req.workspace_parameters.max_corner.z = 1.0
        motion_req.workspace_parameters.min_corner.x = -1.0
        motion_req.workspace_parameters.min_corner.y = -1.0
        motion_req.workspace_parameters.min_corner.z = -1.0

        # create the goal constraints
        if compute_ik:
          (ik_resp, goal) = self.getGoalConstraints(frame,q)

        #if (ik_resp.error_code.val > 0):
        #  return (1,None)

        print ik_resp
        print goal

        motion_req.goal_constraints.append(goal)
        motion_req.group_name = self.group
        motion_req.num_planning_attempts = 10
        motion_req.allowed_planning_time = 4.0
        motion_req.planner_id = "RRTconnectkConfigDefault"
        
        if len(motion_req.goal_constraints[0].joint_constraints) == 0 or (not ik_resp is None and ik_resp.error_code.val < 0):
            return (-31, None)

        goal = MoveGroupGoal()
        goal.planning_options = planning_options
        goal.request = motion_req

        rospy.logwarn( "Sending request...")

        self.client.send_goal(goal)
        self.client.wait_for_result()
        res = self.client.get_result()

        rospy.logwarn("Done: " + str(res.error_code.val))

        #print res

        return (res.error_code.val, res)
